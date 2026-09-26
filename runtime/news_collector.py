"""Run separately from the bot: python -m runtime.news_collector --once."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import time
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .article_body import ArticleBodyFetcher, _valid_host
from .news_sources import SourceClient, SourceError, parse_links, published
from .news_utils import _canonical_url
from .news_store import NewsStore


def load_sources(root):
    config = yaml.safe_load((Path(root) / 'config/news_sources.yaml').read_text(encoding='utf-8')) or {}
    sources = []
    ids = set()
    for raw in config.get('sources', []):
        if not raw.get('enabled', True):
            continue
        s = dict(raw)
        for field in ('id', 'url', 'adapter', 'article_domains', 'publisher_id', 'source_kind', 'source_grade'):
            if not s.get(field):
                raise ValueError(f'missing source field: {field}')
        if s['id'] in ids or s['adapter'] not in ('rss', 'listing', 'sitemap'):
            raise ValueError('duplicate source id or invalid adapter')
        ids.add(s['id'])
        if s['source_kind'] not in ('media', 'official', 'party', 'candidate', 'other'):
            raise ValueError('invalid source kind')
        if s['source_grade'] not in ('A', 'B', 'C', 'D', 'E'):
            raise ValueError('invalid source grade')
        if s['source_kind'] in ('party', 'candidate') and s['source_grade'] not in ('D', 'E'):
            raise ValueError('interested-party sources must remain D/E')
        s['interval_seconds'] = max(60, int(s.get('interval_seconds', 900)))
        s['max_links'] = max(1, min(500, int(s.get('max_links', 100))))
        sources.append(s)
    return sources


class NewsCollector:
    def __init__(self, root, store=None, client=None, body_fetcher=None, clock=time.time, jitter=None):
        self.root = Path(root)
        self.store = store or NewsStore(self.root / 'cache/news/news.sqlite3')
        self.sources = load_sources(root)
        self.by_id = {s['id']: s for s in self.sources}
        domains = {d for s in self.sources for d in s['article_domains']}
        discovery_domains = {urlparse(s['url']).hostname for s in self.sources}
        self.client = client or SourceClient(domains | discovery_domains)
        self.body = body_fetcher or ArticleBodyFetcher(allowed_domains=domains)
        self.clock = clock
        self.jitter = jitter or (lambda: random.uniform(0, 30))
        self.store.register_sources(self.sources, self.clock())

    def import_links(self, path):
        """Import host search/backfill links without trusting supplied grades or prose."""
        records = []
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            source = self.by_id.get(raw.get('source_id'))
            url = _canonical_url(raw.get('url'))
            if not source or not _valid_host(url, source['article_domains']):
                raise ValueError('import URL must match an enabled registered source')
            records.append({'url': url, 'title': str(raw.get('title', ''))[:300],
                            'published_at': published(str(raw.get('published_at') or '')),
                            'source_id': source['id'], 'source_kind': source['source_kind'],
                            'source_grade': source['source_grade'], 'publisher_id': source['publisher_id'],
                            'independence_key': source['publisher_id'], 'source_name': urlparse(url).hostname,
                            'jurisdictions': source.get('jurisdictions', [])})
        for row in records:
            self.store.discover(row, self.clock())
        return len(records)

    def _delay(self, attempts, retry_after=0):
        return max(float(retry_after), min(21600, 60 * 2 ** min(attempts, 9))) + self.jitter()

    def tick(self, max_jobs=40):
        stats = {'discovery_jobs': 0, 'body_jobs': 0, 'failures': 0}
        # Alternate job classes so discovery cannot starve pending bodies.
        for _ in range(max_jobs):
            progressed = False
            for kind in ('discover', 'body'):
                job = self.store.claim(kind, self.clock())
                if job is None:
                    continue
                progressed = True
                now = self.clock()
                url = job['payload'].get('url') or job['key']
                wait_until = self.store.reserve_host(urlparse(url).hostname or '', now)
                if wait_until:
                    self.store.finish(job, wait_until)
                    continue
                if kind == 'discover':
                    stats['discovery_jobs'] += 1
                    source = self.by_id.get(job['key'])
                    if not source:
                        self.store.finish(job)
                        continue
                    state = self.store.source(source['id'])
                    try:
                        data, headers, status = self.client.get(source['url'], state)
                        rows = [] if status == 304 else parse_links(data, source)
                        if status != 304 and not rows:
                            raise SourceError('empty_discovery_requires_review')
                        for row in rows:
                            self.store.discover(row, self.clock())
                        self.store.record_run(source['id'], self.clock(), 'not_modified' if status == 304 else 'ok', len(rows), headers=headers)
                        self.store.finish(job, self.clock() + source['interval_seconds'])
                    except Exception as exc:
                        stats['failures'] += 1
                        reason = str(exc)[:300]
                        self.store.record_run(source['id'], now, 'error', error=reason)
                        self.store.finish(job, self.clock() + self._delay(state.get('failures', 0) + 1, getattr(exc, 'retry_after', 0)), reason)
                else:
                    stats['body_jobs'] += 1
                    article = self.store.article(job['key']) or {}
                    active_origins = set(article.get('discovered_via', [])) & set(self.by_id)
                    if job['payload']['source_id'] not in self.by_id and not active_origins:
                        self.store.finish(job)
                        continue
                    try:
                        body = self.body.fetch(job['key'])
                    except Exception as exc:
                        body = {'body_status': 'fetch_failed', 'error': type(exc).__name__}
                    self.store.save_body(job['key'], body, self.clock())
                    status = body.get('body_status', 'fetch_failed')
                    if status == 'read':
                        # Recheck recent articles for corrections; preserve every version.
                        article = self.store.article(job['key'])
                        first = dt.datetime.fromisoformat(article['first_seen_at']).timestamp()
                        self.store.finish(job, self.clock() + 21600 if now - first < 7 * 86400 else None)
                    elif status in ('unsupported_domain', 'unsafe_dns', 'robots_denied', 'access_restricted', 'http_404', 'http_410'):
                        self.store.finish(job, error=status)
                    else:
                        stats['failures'] += 1
                        self.store.finish(job, self.clock() + self._delay(job['attempts'], body.get('retry_after_seconds', 0)), status)
            if not progressed:
                break
        return stats


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--research-requests', action='store_true', help='print pending host research questions as JSONL')
    parser.add_argument('--import-links', type=Path, help='JSONL discovery links from host search or backfill')
    parser.add_argument('--refresh', action='store_true', help='make healthy discovery sources due now')
    parser.add_argument('--max-jobs', type=int, default=40)
    args = parser.parse_args(argv)
    if args.research_requests:
        store = NewsStore(args.repo_root / 'cache/news/news.sqlite3')
        for row in store.research_requests():
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if args.status:
        print(json.dumps(NewsStore(args.repo_root / 'cache/news/news.sqlite3').health(), ensure_ascii=False, indent=2))
        return 0
    collector = NewsCollector(args.repo_root)
    if args.import_links:
        print(json.dumps({"imported_links": collector.import_links(args.import_links)}))
    if args.refresh:
        collector.store.refresh(collector.clock())
    try:
        while True:
            print(json.dumps(collector.tick(max(1, min(500, args.max_jobs))), ensure_ascii=False), flush=True)
            if args.once:
                return 0
            time.sleep(10)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
