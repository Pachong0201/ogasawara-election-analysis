"""Network-free RetrievalBackend backed by the durable local news corpus."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import time
from pathlib import Path

from .campaign_event import _norm_text
from .news_store import NewsStore
from .source_registry import RetrievalBackend

TAIPEI_TZ = dt.timezone(dt.timedelta(hours=8))


def cutoff_time(as_of=None):
    if not as_of:
        return time.time()
    text = str(as_of)
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        return (dt.datetime.fromisoformat(text).replace(tzinfo=TAIPEI_TZ) + dt.timedelta(days=1)).timestamp() - 0.000001
    date = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    return date.replace(tzinfo=date.tzinfo or TAIPEI_TZ).timestamp()


def article_date(record):
    for key in ('page_date', 'published_at', 'publish_date'):
        if record.get(key):
            try:
                value = str(record[key])
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    value += "T00:00:00+08:00"
                return cutoff_time(value)
            except ValueError:
                pass
    return None


def relevant(record, jurisdiction, candidate_names):
    text = _norm_text(str(record.get('title', '')) + ' ' + str(record.get('content', '')))
    county = _norm_text(jurisdiction)
    aliases = {county}
    # Hsinchu/Chiayi without city/county is ambiguous and must not be guessed.
    if county and not county.startswith(('新竹', '嘉義', '嘉义')):
        aliases.add(county.rstrip('县市'))
    location = any(alias and alias in text for alias in aliases)
    candidate = any(_norm_text(name) in text for name in candidate_names if name)
    tagged = county in {_norm_text(c) for c in record.get('jurisdictions', [])}
    election = any(_norm_text(word) in text for word in ('選舉', '選情', '參選', '候選人', '競選', '提名', '退選', '民調', '輔選'))
    return candidate or ((location or tagged) and election)


class LocalNewsBackend(RetrievalBackend):
    supports_offline = True

    def __init__(self, repo_root, store=None, max_results=300, queue_research=True, research_coordinator=None):
        self.store = store or NewsStore(Path(repo_root) / 'cache/news/news.sqlite3')
        self.max_results = max_results
        self.queue_research = queue_research
        self.research_coordinator = research_coordinator
        self._seen = {}
        self._truncated = False

    def search(self, query, **kwargs):
        jurisdiction = str(kwargs.get('jurisdiction') or '')
        if not jurisdiction:
            return []
        as_of = kwargs.get('as_of')
        if not as_of:
            match = re.search(r'截至(\d{4}-\d{2}-\d{2})', query)
            as_of = match.group(1) if match else None
        cutoff = min(time.time(), cutoff_time(as_of))
        days = kwargs.get('recency_days', 3650 if kwargs.get('purpose') == 'local_knowledge' else 30)
        start = cutoff - max(1, min(3650, int(days))) * 86400
        rows = []
        for record in self.store.records_at(cutoff):
            date = article_date(record)
            if date is None or not start <= date <= cutoff:
                continue
            if not relevant(record, jurisdiction, kwargs.get('candidate_names', [])):
                continue
            # Always preserve source kind/grade; the resolver controls evidence use.
            rows.append({**record, 'query': query, 'jurisdiction': jurisdiction})
        if self.queue_research and self.research_coordinator is None and (not rows or kwargs.get('purpose') == 'local_knowledge'):
            # Durable host handoff, never an implicit paid API request.
            key = hashlib.sha256(f"{jurisdiction}:{str(as_of)[:10]}:{query}".encode()).hexdigest()
            self.store.enqueue('research', key, {'query': query, 'jurisdiction': jurisdiction,
                               'as_of': as_of, 'reason': 'research_question' if rows else 'coverage_gap'}, time.time())
        rows.sort(key=lambda r: (article_date(r) or 0, r['url']), reverse=True)
        self._truncated = self._truncated or len(rows) > self.max_results
        rows = rows[:self.max_results]
        self._seen.update({r["url"]: r for r in rows})
        return rows

    def fetch(self, url):
        # Never exposes a newer version than the one selected by search/as_of.
        return dict(self._seen[url]) if url in self._seen else None

    def enrich(self, leads):
        return [dict(row) for row in leads]

    def resolve_events(self, events, jurisdiction, target_year, election_type, as_of):
        # Historical replays must not mutate the live event identity index.
        if dt.datetime.fromtimestamp(cutoff_time(as_of), TAIPEI_TZ).date() < dt.datetime.now(TAIPEI_TZ).date():
            return events
        scope = f'{jurisdiction}:{target_year}:{election_type}'
        return self.store.reconcile_events(events, scope, time.time())

    def metadata(self):
        return {'backend': 'local_news', 'network_requests': 0, 'lead_only': True,
                'result_truncated': self._truncated, **self.store.health()}


def build_retrieval_backend(provider, repo_root, mode='online', max_body_fetches=6):
    if provider == 'local':
        if mode != 'offline':
            from .research_providers import ResearchConfig
            config = ResearchConfig.from_env()
            if config.enabled:
                from .auto_research import ResearchCoordinator
                from .research_store import ResearchStore
                store = ResearchStore(Path(repo_root) / 'cache/news/news.sqlite3')
                coordinator = ResearchCoordinator(repo_root, store, config)
                return LocalNewsBackend(repo_root, store, research_coordinator=coordinator)
        return LocalNewsBackend(repo_root, queue_research=mode != "offline")
    if provider == 'gdelt' and mode != 'offline':
        from .gdelt_retrieval import GDELTNewsBackend
        return GDELTNewsBackend(max_body_fetches=max_body_fetches)
    return None
