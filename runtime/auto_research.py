"""Bounded automatic research, reusable by the analysis pipeline and daemon.

Model output is never executable. Only locally validated search plans and
quotes backed by fetched bodies enter the result. No knowledge promotion.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .article_body import ArticleBodyFetcher, _valid_host
from .campaign_event import _norm_text
from .news_sources import published
from .news_store import dumps, iso
from .news_utils import _canonical_url
from .research_providers import (
    MODEL_MAX_TOKENS, GoModel, TavilySearch, ResearchConfig, ProviderError, SYSTEM,
)
from .research_store import ResearchStore


def strings(value, limit=12):
    return list(dict.fromkeys(s.strip()[:500] for s in value if isinstance(s, str) and s.strip()))[:limit] if isinstance(value, list) else []


def queries(value, county):
    result = []
    for item in value[:6] if isinstance(value, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get('query'), str):
            continue
        query = item['query'].strip()[:300]
        if not query:
            continue
        if _norm_text(county) not in _norm_text(query):
            query = county + ' ' + query
        row = {'query': query, 'purpose': 'background' if item.get('purpose') == 'background' else 'news'}
        if row not in result:
            result.append(row)
    return result


class ResearchWorker:
    def __init__(self, root, store=None, config=None, model=None, search=None, body_factory=None):
        self.root = Path(root)
        self.store = store or ResearchStore(self.root / 'cache/news/news.sqlite3')
        self.config = config or ResearchConfig.from_env()
        self.model = model or GoModel(self.config)
        self.search = search or TavilySearch(self.config)
        self.body_factory = body_factory or (lambda host: ArticleBodyFetcher(allowed_domains=[host]))
        path = self.root / 'config/research_sources.yaml'
        if not path.exists():
            path = Path(__file__).resolve().parents[1] / 'config/research_sources.yaml'
        source_config = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        self.publishers = source_config.get('publishers', [])
        self.official_domains = tuple(source_config.get('official_domains') or ())

    def _identity(self, url):
        host = urlparse(url).hostname
        if host and _valid_host(url, self.official_domains):
            official_id = 'official_' + host.lower().replace('.', '_')
            return dict(source_id=official_id, publisher_id=official_id,
                        source_kind='official', source_grade='A',
                        independence_key=host.lower())
        for publisher in self.publishers:
            if _valid_host(url, publisher['domains']):
                return dict(source_id='web_' + publisher['id'], publisher_id=publisher['id'],
                            source_kind='media', source_grade='C', independence_key=publisher['id'])
        # Official/party identities require explicit classification; never auto-promote to A/B.
        return dict(source_id='web_' + host, publisher_id=host, source_kind='other',
                    source_grade='E', independence_key=host)

    def _remaining(self, cap=20):
        remaining = self.deadline - time.monotonic()
        if remaining <= 1:
            raise ProviderError('research_deadline', retryable=False)
        return min(cap, remaining)

    def _model(self, job, state, payload):
        state['active_stage'] = 'model_' + str(payload.get('stage') or 'unknown')
        self.store.checkpoint(job, state)
        timeout = self._remaining(105)
        # UTF-8 bytes are a conservative token reservation for these text requests.
        reserve = len((SYSTEM + dumps(payload)).encode()) + MODEL_MAX_TOKENS.get(
            str(payload.get('stage') or ''), 12000
        )
        call = self.store.reserve_call(job, 'model', reserve, self.config.daily_tokens,
                                       {'stage': payload['stage'], 'model': self.config.model})
        if call is None:
            raise ProviderError('daily_model_budget', 86400 - time.time() % 86400)
        try:
            output, tokens = self.model.complete(payload, f'ogasawara-research-{job["id"]}', timeout)
            self.store.finish_call(call, 'ok', tokens)
            state['model_tokens'] = state.get('model_tokens', 0) + tokens
            return output
        except Exception:
            self.store.finish_call(call, 'failed')
            raise

    def _read(self, job, state, task, row, purpose):
        state['active_stage'] = 'body_read'
        raw_url = row.get('url')
        if not isinstance(raw_url, str) or len(raw_url) > 4000:
            return
        try:
            raw_host = urlparse(raw_url).hostname
            if not raw_host or not _valid_host(raw_url, [raw_host]):
                return
        except ValueError:
            return
        url = _canonical_url(raw_url)
        host = urlparse(url).hostname
        if not host or not _valid_host(url, [host]):
            return
        if any(r['url'] == url for r in state['articles']):
            return
        self.store.checkpoint(job, state)
        self._remaining()
        record = dict(url=url, title=str(row.get('title') or '')[:300],
                      published_at=published(str(row.get('published_date') or row.get('published_at') or '')),
                      source_name=host, **self._identity(url))
        existing = self.store.article(url)
        from .news_retrieval import cutoff_time
        fresh = existing and time.time() - cutoff_time(existing.get('retrieved_at')) < self.config.cache_seconds
        body = existing if fresh and existing.get('body_status') == 'read' else None
        if body is None:
            reader = self.body_factory(host)
            if hasattr(reader, 'timeout'):
                reader.timeout = min(5, max(1, self._remaining() / 8))
            next_allowed = self.store.reserve_host(host, time.time(), 2)
            if next_allowed:
                body = {'body_status': 'host_backoff'}
            else:
                body = reader.fetch(url)
        # DNS/body reading can be slow; verify ownership again before persisting evidence.
        self.store.checkpoint(job, state)
        record.update(body)
        text = _norm_text(str(record.get('content') or ''))
        county = _norm_text(task['jurisdiction'])
        names = strings(task.get('candidate_names', []), 30)
        in_scope = county in text or any(_norm_text(name) in text for name in names)
        if not in_scope and record.get('body_status') == 'read':
            state['articles'].append({'url': url, 'body_status': 'out_of_scope'})
            return
        from .news_retrieval import article_date, cutoff_time
        date = article_date(record)
        if date is not None and date > min(time.time(), cutoff_time(task['end_date'])):
            state['articles'].append({'url': url, 'body_status': 'future_publication'})
            return
        self.store.discover({k: v for k, v in record.items() if k not in ('content', 'body_status')}, time.time(), enqueue_body=False)
        self.store.save_body(url, body, time.time())
        article = self.store.article(url)
        state['articles'].append({'url': url, 'body_status': article['body_status'], 'purpose': purpose})
        if article.get('body_status') == 'read':
            # Body excerpt sent to the model is recorded so citation validation is reproducible.
            state['evidence'].append({k: article.get(k) for k in (
                'url', 'title', 'source_id', 'source_name', 'source_kind', 'source_grade',
                'publisher_id', 'independence_key', 'page_date', 'published_at',
                'article_version', 'retrieved_at')}
                | {'content': article.get('content', '')[:2400],
                   'content_truncated': bool(article.get('content_truncated')) or len(article.get('content', '')) > 2400})

    def _search_round(self, job, state, task, plan):
        for item in plan:
            signature = dumps(item)
            if signature in state['completed_queries']:
                continue
            if len(state['completed_queries']) >= self.config.max_queries:
                break
            self.store.checkpoint(job, state)
            timeout = self._remaining()
            cached = state['search_results'].get(signature)
            if cached is None:
                state['active_stage'] = 'search'
                call = self.store.reserve_call(job, 'search', 1, self.config.daily_searches, item)
                if call is None:
                    raise ProviderError('daily_search_budget', 86400 - time.time() % 86400)
                try:
                    rows = self.search.search(item['query'], item['purpose'], task['end_date'], timeout)
                    self.store.finish_call(call, 'ok')
                except Exception:
                    self.store.finish_call(call, 'failed')
                    raise
                cached = [{k: str(r.get(k) or '')[:4000] for k in ('url', 'title', 'content', 'published_date', 'published_at')} for r in rows]
                state['search_results'][signature] = cached
                self.store.checkpoint(job, state)
            for row in cached:
                if len(state['articles']) >= self.config.max_bodies:
                    break
                self._read(job, state, task, row, item['purpose'])
                self.store.checkpoint(job, state)
            state['completed_queries'].append(signature)
            self.store.checkpoint(job, state)

    @staticmethod
    def validate_findings(output, evidence):
        by_url = {r['url']: r for r in evidence}
        findings, rejected = [], 0
        for finding in (output.get('findings') or [])[:12] if isinstance(output.get('findings'), list) else []:
            if not isinstance(finding, dict) or not isinstance(finding.get('statement'), str):
                rejected += 1
                continue
            citations = []
            raw = finding.get('citations')
            for cite in raw[:6] if isinstance(raw, list) else []:
                if not isinstance(cite, dict) or not isinstance(cite.get('url'), str):
                    continue
                article = by_url.get(cite['url'])
                quote = cite.get('quote')
                if article and isinstance(quote, str) and 16 <= len(quote) <= 800 and quote in article['content']:
                    citations.append({k: article.get(k) for k in (
                        'url', 'title', 'source_id', 'source_name', 'source_grade',
                        'source_kind', 'publisher_id', 'independence_key', 'page_date',
                        'article_version')}
                                     | {'quote': quote})
            # All supplied citations must be real; partial validation cannot rescue a fabricated citation.
            if citations and isinstance(raw, list) and len(citations) == len(raw):
                findings.append({'statement': finding['statement'][:700], 'citations': citations,
                                 'question': finding.get('question', '')[:500] if isinstance(finding.get('question', ''), str) else '',
                                 'verification_status': 'body_grounded_unverified'})
            else:
                rejected += 1
        return findings, rejected

    def tick(self, job_id=None):
        if not self.config.enabled:
            return {'status': 'disabled'}
        if self.config.problem():
            return {'status': 'configuration_error', 'last_error': self.config.problem()}
        job = self.store.claim_research(job_id)
        if not job:
            return self.store.result(job_id) if job_id else {'status': 'idle'}
        self.deadline = time.monotonic() + self.config.job_seconds
        state = self.store.result(job['id'])
        state.update(status='running', model=self.config.model, search_provider='tavily')
        state.pop('last_error', None)
        state.pop('error_stage', None)
        for key, default in [('articles', []), ('evidence', []), ('completed_queries', []), ('search_results', {}), ('findings', []), ('unresolved', [])]:
            state.setdefault(key, default)
        task = dict(job['payload'])
        task.setdefault('questions', [task.get('query', '')])
        task.setdefault('end_date', dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat())
        state['task'] = task
        try:
            if not task.get('jurisdiction'):
                raise ProviderError('missing_research_jurisdiction', retryable=False)
            from .news_retrieval import cutoff_time
            today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
            if task.get('as_of') and dt.datetime.fromtimestamp(cutoff_time(task['as_of']), dt.timezone(dt.timedelta(hours=8))).date() < today:
                state['status'] = 'historical_replay'
                self.store.checkpoint(job, state)
                self.store.finish(job)
                return self.store.result(job['id'])
            if not state.get('plan'):
                output = self._model(job, state, {'stage': 'plan', 'task': task, 'max_queries': 4})
                plan = queries(output.get('queries'), task['jurisdiction'])[:4]
                # A successful run always makes an actual search, even if the plan is malformed.
                state['plan'] = plan or [{'query': task['jurisdiction'] + ' ' + str(task.get('target_year', '')) + ' 選舉 最新 提名 支持 澄清', 'purpose': 'news'}]
                if not any(item['purpose'] == 'news' for item in state['plan']):
                    state['plan'] = [{'query': task['jurisdiction'] + ' 選舉 最新動態', 'purpose': 'news'}] + state['plan'][:3]
                election = {'county_mayor': '縣市長', 'president': '總統', 'regional_legislator': '區域立委'}.get(task.get('election_type'), '')
                for item in state['plan']:
                    if item['purpose'] == 'news':
                        item['query'] += ' ' + str(task.get('target_year', '')) + ' ' + election
            self._search_round(job, state, task, state['plan'])
            if not state.get('review'):
                # Review sees only the most recent evidence window; the output
                # (findings + citations) scales with the input and otherwise
                # hits the model's output ceiling on large rounds.
                review_evidence = state['evidence'][-8:]
                state['review'] = self._model(job, state, {
                    'stage': 'review', 'task': task, 'evidence': review_evidence,
                    'max_followup_queries': 2, 'max_findings': 3,
                })
                state['findings'], state['rejected_findings'] = self.validate_findings(state['review'], review_evidence)
                state['unresolved'] = strings(state['review'].get('unresolved'))
                self.store.checkpoint(job, state)
            followups = queries(state['review'].get('queries'), task['jurisdiction'])[:2]
            self._search_round(job, state, task, followups)
            final = state['review']
            if followups:
                review_evidence = state['evidence'][-8:]
                final = self._model(job, state, {
                    'stage': 'review', 'task': task, 'evidence': review_evidence,
                    'max_followup_queries': 0, 'max_findings': 3,
                })
            else:
                review_evidence = state['evidence'][-8:]
            state['findings'], state['rejected_findings'] = self.validate_findings(final, review_evidence)
            state['unresolved'] = strings(final.get('unresolved'))
            state['questions'] = strings(task.get('questions'))
            answered = {item['question'] for item in state['findings'] if item['question']}
            for question in state['questions']:
                if question not in answered:
                    state['unresolved'].append('尚未建立問題證據：' + question)
            state['status'] = 'completed' if state['findings'] else 'insufficient_evidence'
            if state['rejected_findings']:
                state['unresolved'].append('部分模型結論缺少可對應原文的引用，已排除。')
            if not state['findings']:
                state['unresolved'].append('本次未取得足夠可引用證據，不能據此判定事件不存在。')
            state['completed_at'] = iso(time.time())
            state.pop('active_stage', None)
            self.store.checkpoint(job, state)
            self.store.finish(job)
        except Exception as exc:
            code = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
            state['status'] = 'partial' if state['evidence'] else 'failed'
            state['last_error'] = code
            state['error_stage'] = str(state.get('active_stage') or 'unknown')
            retryable = isinstance(exc, ProviderError) and exc.retryable and job['attempts'] < 3
            base_delay = 15 if code in {'provider_timeout', 'provider_transport_error'} else 60
            delay = max(base_delay * 2 ** max(0, job['attempts'] - 1),
                        getattr(exc, 'retry_after', 0))
            try:
                self.store.checkpoint(job, state)
                self.store.finish(job, time.time() + delay if retryable else None, code)
            except RuntimeError:
                return {'status': 'lease_lost'}
        return self.store.result(job['id'])


def public_result(result):
    """Keep full bodies and raw search responses out of the report writer context."""
    return {k: result[k] for k in ('status', 'job_id', 'model', 'search_provider', 'findings',
            'unresolved', 'questions', 'last_error', 'error_stage', 'updated_at',
            'completed_at', 'model_tokens', 'rejected_findings') if k in result} | {
                'search_count': len(result.get('search_results', {})),
                'body_count': len(result.get('evidence', [])),
                'coverage_complete': False,
            }


class ResearchCoordinator:
    def __init__(self, root, store, config=None, worker_factory=None):
        self.root, self.store = Path(root), store
        self.config = config or ResearchConfig.from_env()
        self.worker_factory = worker_factory or (lambda: ResearchWorker(root, store, self.config))

    def research(self, task, questions, candidate_names):
        if not self.config.enabled:
            return {'status': 'disabled'}
        if self.config.problem():
            return {'status': 'configuration_error', 'last_error': self.config.problem()}
        payload = {'jurisdiction': task.jurisdiction, 'target_year': task.target_year,
                   'election_type': task.election_type, 'questions': sorted(strings(questions)),
                   'candidate_names': sorted(strings(candidate_names, 30)),
                   'objective': ('更新近30日競選動態，並補查以下地方政治研究問題。'
                                 '優先查找政府機關原始公告，再以獨立媒體正文補充或交叉核驗。')}
        key = 'auto-v1-' + hashlib.sha256(dumps({**payload, 'model': self.config.model, 'base_url': self.config.base_url}).encode()).hexdigest()
        payload['end_date'] = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
        job_id = self.store.schedule(key, payload, self.config.cache_seconds)
        worker = self.worker_factory()
        # A foreground timeout does not cancel durable work. The collector can resume after exit.
        def run_pending():
            deadline = time.monotonic() + self.config.job_seconds
            while time.monotonic() < deadline:
                current = worker.tick(job_id)
                if current.get('status') not in ('running', 'pending', 'retry_pending'):
                    return
                time.sleep(1)
        thread = threading.Thread(target=run_pending, daemon=True, name='election-research')
        thread.start()
        thread.join(self.config.foreground_seconds)
        result = public_result(self.store.result(job_id))
        result['cache_ttl_seconds'] = self.config.cache_seconds
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args(argv)
    from bot.config import _load_dotenv
    _load_dotenv(args.repo_root / '.env')
    worker = ResearchWorker(args.repo_root)
    if args.status:
        print(dumps({'enabled': worker.config.enabled, 'configuration_error': worker.config.problem(), **worker.store.health()}))
        return 0
    if not worker.config.enabled or worker.config.problem():
        print(dumps({'status': 'configuration_error', 'reason': worker.config.problem() or 'research_disabled'}))
        return 2
    try:
        while True:
            print(dumps(public_result(worker.tick())), flush=True)
            if args.once:
                return 0
            time.sleep(5)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
