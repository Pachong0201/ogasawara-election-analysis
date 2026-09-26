"""Behavioral acceptance of the automatic research path without paid API calls."""
import asyncio
import datetime as dt
import json
import time
import threading
from dataclasses import replace
from unittest.mock import patch

import pytest

from bot.models import ParsedRequest, ElectionFocus
from bot.report_writer import DeterministicReportWriter
from runtime.auto_research import ResearchCoordinator, ResearchWorker, public_result
from runtime.news_retrieval import LocalNewsBackend, build_retrieval_backend, cutoff_time
from runtime.pipeline import AnalysisPipeline
from runtime.research_providers import GoModel, TavilySearch, ResearchConfig, ProviderError
from runtime.research_store import ResearchStore
from runtime.source_registry import SourceRegistry
from tests.fixtures.helpers import populate_full_repo, make_task

QUOTE = '高雄市候選人宣布競選總部成立，並表示將展開地方選舉活動。'
URL = 'https://www.newtalk.tw/news/1'


def config(**changes):
    return replace(ResearchConfig(enabled=True, api_key='test-key', search_key='test-search', foreground_seconds=2), **changes)


def setup(tmp_path, cfg=None, model=None, search=None, body_factory=None):
    store = ResearchStore(tmp_path / 'news.sqlite3')
    model, search = model or Model(), search or Search()
    worker = ResearchWorker(tmp_path, store, cfg or config(), model, search, body_factory or (lambda host: Body()))
    task = dict(jurisdiction='高雄市', target_year=2026, election_type='county_mayor',
                questions=['候選人最新動向'], end_date=dt.date.today().isoformat())
    job_id = store.schedule('scope', task, 1800)
    return store, worker, model, search, job_id


class Model:
    def __init__(self, followup=False, bad_citation=False):
        self.calls = []
        self.followup, self.bad_citation = followup, bad_citation

    def complete(self, payload, session, timeout):
        self.calls.append((payload, session))
        if payload['stage'] == 'plan':
            return {'queries': [{'query': '高雄市 選舉', 'purpose': 'news'}]}, 30
        evidence = payload['evidence']
        return {'findings': [{'statement': '该网站报道竞选总部成立。', 'citations': [
            {'url': 'https://invented.test/no' if self.bad_citation else r['url'], 'quote': r['content'][:len(QUOTE)]}
        ]} for r in evidence], 'unresolved': [], 'queries': [
            {'query': '高雄市 地方派系 更正', 'purpose': 'background'}] if self.followup else []}, 40


class Search:
    def __init__(self, url=URL, error=None):
        self.calls, self.url, self.error = [], url, error

    def search(self, query, purpose, end_date, timeout):
        self.calls.append((query, purpose, end_date))
        if self.error:
            raise self.error
        return [{'url': self.url, 'title': '高雄市競選總部成立', 'content': '搜尋摘要不可作證據'}]


class Body:
    def fetch(self, url):
        return {'body_status': 'read', 'content': QUOTE * 8,
                'page_date': (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()}


def test_go_protocol_no_native_web_search_and_stable_session():
    calls = []
    def transport(*args):
        calls.append(args)
        return {'choices': [{'message': {'content': '{"queries":[]}'}}], 'usage': {'total_tokens': 12}}
    client = GoModel(config(), transport)
    assert client.complete({'stage': 'plan'}, 'session-1') == ({'queries': []}, 12)
    url, key, body, timeout, headers = calls[0]
    assert url == 'https://opencode.ai/zen/go/v1/chat/completions'
    assert body['model'] == 'glm-5.3-flash' and 'tools' not in body
    assert headers['x-opencode-session'] == 'session-1'
    assert key == 'test-key'


def test_go_rejects_truncated_and_empty_responses():
    import pytest
    for choice, code in [
        ({'finish_reason': 'length', 'message': {'content': '{"findings":[]}'}}, 'model_output_truncated'),
        ({'finish_reason': 'stop', 'message': {'content': None}}, 'model_empty_content'),
    ]:
        client = GoModel(config(), lambda *args: {'choices': [choice]})
        with pytest.raises(ProviderError, match=code):
            client.complete({'stage': 'review'}, 'test', 90)


def test_go_respects_remaining_deadline_and_reserves_thinking_output():
    calls = []
    def transport(*args):
        calls.append(args)
        return {'choices': [{'message': {'content': '{"queries":[]}'}}]}
    GoModel(config(), transport).complete({'stage': 'plan'}, 'test', 5)
    assert calls[0][3] == 5
    assert calls[0][2]['max_tokens'] == 16000
    assert 'thinking' not in calls[0][2]  # GLM-5.3 forces thinking.


def test_tavily_real_request_time_windows_and_no_generated_answer():
    calls = []
    def transport(*args):
        calls.append(args)
        return {'results': []}
    search = TavilySearch(config(), transport)
    search.search('高雄市', 'news', '2026-09-26')
    search.search('高雄市派系', 'background', '2026-09-26')
    assert calls[0][0] == 'https://api.tavily.com/search'
    assert calls[0][1] == 'test-search'
    assert calls[0][2]['start_date'] == '2026-08-27'
    assert calls[0][2]['include_answer'] is False
    assert 'start_date' not in calls[1][2]


def test_cross_media_read_store_and_cited_findings(tmp_path):
    store, worker, model, search, job_id = setup(tmp_path)
    before = time.time()
    result = worker.tick(job_id)
    assert result['status'] == 'completed'
    assert len(search.calls) == 1
    assert store.article(URL)['source_grade'] == 'C'
    assert store.article(URL)['source_id'] == 'web_newtalk'
    assert result['findings'][0]['citations'][0]['quote'] == QUOTE
    assert result['findings'][0]['verification_status'] == 'body_grounded_unverified'
    assert len(store.records_at(time.time())) == 1
    assert store.records_at(before) == []
    assert 'evidence' not in public_result(result)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM research_calls WHERE status='ok'").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE kind='body'").fetchone()[0] == 0
    assert len({session for _, session in model.calls}) == 1


def test_unknown_site_remains_e_even_with_readable_body(tmp_path):
    url = 'https://unknown.example/article'
    store, worker, _, _, job_id = setup(tmp_path, search=Search(url))
    result = worker.tick(job_id)
    assert result['status'] == 'completed'
    assert store.article(url)['source_grade'] == 'E'
    assert store.article(url)['source_kind'] == 'other'


@pytest.mark.parametrize('url', ['http://example.com/a', 'https://user:pass@example.com/a', 'https://example.com:81/a', 'not-a-url'])
def test_invalid_discovered_urls_never_reach_reader(tmp_path, url):
    def forbidden(host):
        pytest.fail('unsafe URL reached body reader')
    store, worker, _, _, job_id = setup(tmp_path, search=Search(url), body_factory=forbidden)
    assert worker.tick(job_id)['status'] == 'insufficient_evidence'
    assert store.health()['article_count'] == 0


def test_fake_citation_and_nonverbatim_quote_are_rejected(tmp_path):
    _, worker, _, _, job_id = setup(tmp_path, model=Model(bad_citation=True))
    result = worker.tick(job_id)
    assert result['status'] == 'insufficient_evidence'
    assert result['rejected_findings'] == 1 and result['findings'] == []
    findings, rejected = worker.validate_findings({'findings': [{'statement': 'invented', 'citations': [
        {'url': URL, 'quote': '不是原文的虛構內容而且字數足夠長不應通過測試'}]}]}, [{'url': URL, 'content': QUOTE}])
    assert findings == [] and rejected == 1


def test_unread_body_cannot_be_replaced_with_search_excerpt(tmp_path):
    class Denied:
        def fetch(self, url):
            return {'body_status': 'robots_denied'}
    store, worker, _, _, job_id = setup(tmp_path, body_factory=lambda host: Denied())
    result = worker.tick(job_id)
    assert result['evidence'] == [] and result['findings'] == []
    assert store.article(URL)['body_status'] == 'robots_denied'
    assert not store.article(URL).get('content')


def test_followup_is_bounded_and_duplicate_articles_are_not_reread(tmp_path):
    _, worker, model, search, job_id = setup(tmp_path, model=Model(followup=True))
    result = worker.tick(job_id)
    assert len(search.calls) == 2 and search.calls[1][1] == 'background'
    assert len(model.calls) == 3 and len(result['evidence']) == 1


def test_429_persistent_retry_after_and_recovery(tmp_path):
    store, worker, model, search, job_id = setup(tmp_path, search=Search(error=ProviderError('provider_http_429', 3600)))
    result = worker.tick(job_id)
    assert result['status'] == 'retry_pending'
    with store.connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        assert row['due'] >= time.time() + 3590
        db.execute('UPDATE jobs SET due=0 WHERE id=?', (job_id,))
    search.error = None
    assert worker.tick(job_id)['status'] == 'completed'
    assert sum(payload['stage'] == 'plan' for payload, _ in model.calls) == 1


def test_resume_after_review_failure_does_not_repeat_search(tmp_path):
    class FailingModel(Model):
        fail = True
        def complete(self, payload, session, timeout):
            if payload['stage'] == 'review' and self.fail:
                raise ProviderError('provider_transport_or_json_error')
            return super().complete(payload, session, timeout)
    model = FailingModel()
    store, worker, _, search, job_id = setup(tmp_path, model=model)
    assert worker.tick(job_id)['status'] == 'retry_pending'
    with store.connect() as db:
        db.execute('UPDATE jobs SET due=0 WHERE id=?', (job_id,))
    model.fail = False
    assert worker.tick(job_id)['status'] == 'completed'
    assert len(search.calls) == 1


def test_daily_budget_is_shared_and_no_request_when_exhausted(tmp_path):
    store, worker, _, search, job_id = setup(tmp_path, cfg=config(daily_tokens=1))
    result = worker.tick(job_id)
    assert result['status'] == 'retry_pending'
    assert result['last_error'] == 'daily_model_budget'
    assert search.calls == []


def test_single_worker_and_expired_lease_owner_cannot_commit(tmp_path):
    store, _, _, _, job_id = setup(tmp_path)
    first = store.claim_research(job_id)
    assert store.claim_research(job_id) is None
    with store.connect() as db:
        db.execute('UPDATE jobs SET lease_until=0 WHERE id=?', (job_id,))
    second = store.claim_research(job_id)
    assert second['token'] != first['token']
    with pytest.raises(RuntimeError, match='lease_lost'):
        store.checkpoint(first, {'status': 'bad'})
    store.checkpoint(second, {'status': 'good'})


def test_completed_scope_cache_avoids_paid_calls(tmp_path):
    store, worker, _, search, _ = setup(tmp_path)
    coordinator = ResearchCoordinator(tmp_path, store, config(), lambda: worker)
    a = coordinator.research(make_task(jurisdiction='高雄市'), ['問題'], [])
    calls = len(search.calls)
    b = coordinator.research(make_task(jurisdiction='高雄市'), ['問題'], [])
    assert a['status'] == b['status'] == 'completed'
    assert a['job_id'] == b['job_id'] and len(search.calls) == calls


def test_foreground_timeout_leaves_background_job_running(tmp_path):
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    class SlowSearch(Search):
        def search(self, *args):
            entered.set()
            assert release.wait(3)
            return super().search(*args)
    store, worker, _, _, _ = setup(tmp_path, search=SlowSearch())
    original_tick = worker.tick
    def tick(job_id):
        try:
            return original_tick(job_id)
        finally:
            completed.set()
    worker.tick = tick
    coordinator = ResearchCoordinator(tmp_path, store, config(foreground_seconds=0.02), lambda: worker)
    result = coordinator.research(make_task(jurisdiction='高雄市'), ['問題'], [])
    assert entered.wait(1)
    assert result['status'] == 'running'
    release.set()
    assert completed.wait(3)
    assert store.result(result['job_id'])['status'] == 'completed'


def test_search_budget_and_auth_errors_do_not_loop(tmp_path):
    store, worker, _, search, job_id = setup(tmp_path, cfg=config(daily_searches=1), model=Model(followup=True))
    result = worker.tick(job_id)
    assert result['status'] == 'retry_pending' and result['last_error'] == 'daily_search_budget'
    assert len(search.calls) == 1
    # A new task shares the same daily ledger.
    other = store.schedule('other', {'jurisdiction': '高雄市'}, 1800)
    assert worker.tick(other)['last_error'] == 'daily_search_budget'
    assert len(search.calls) == 1
    with store.connect() as db:
        db.execute('DELETE FROM research_calls')
    search.error = ProviderError('provider_http_401', retryable=False)
    third = store.schedule('auth', {'jurisdiction': '高雄市'}, 1800)
    assert worker.tick(third)['status'] == 'failed'
    worker.tick(third)
    assert len(search.calls) == 2


def test_future_and_other_county_bodies_are_excluded(tmp_path):
    class WrongBody:
        def fetch(self, url):
            return {'body_status': 'read', 'content': '新竹縣候選人參選活動。' * 20,
                    'page_date': dt.date.today().isoformat()}
    store, worker, _, _, job_id = setup(tmp_path, body_factory=lambda host: WrongBody())
    assert worker.tick(job_id)['status'] == 'insufficient_evidence'
    assert not store.article(URL)
    class FutureBody(Body):
        def fetch(self, url):
            return {**super().fetch(url), 'page_date': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()}
    worker.body_factory = lambda host: FutureBody()
    next_id = store.schedule('future', {'jurisdiction': '高雄市'}, 1800)
    # Clear per-host spacing to exercise date validation, not the backoff path.
    with store.connect() as db:
        db.execute('DELETE FROM host_limits')
    assert worker.tick(next_id)['status'] == 'insufficient_evidence'
    assert not store.article(URL)


def test_offline_factory_never_installs_research(monkeypatch, tmp_path):
    monkeypatch.setenv('OGASAWARA_AUTO_RESEARCH', 'true')
    backend = build_retrieval_backend('local', tmp_path, mode='offline')
    assert backend.research_coordinator is None and backend.queue_research is False


def test_missing_keys_produce_explicit_configuration_status(tmp_path):
    store = ResearchStore(tmp_path / 'news.sqlite3')
    coordinator = ResearchCoordinator(tmp_path, store, config(api_key=''))
    assert coordinator.research(make_task(), [], [])['status'] == 'configuration_error'


def test_pipeline_rebuild_includes_newly_observed_body_and_no_prepass_snapshot(tmp_path):
    populate_full_repo(tmp_path)
    store = ResearchStore(tmp_path / 'news.sqlite3')
    class Coordinator:
        calls = 0
        def research(self, task, questions, candidate_names):
            self.calls += 1
            assert not list((tmp_path / 'cache').rglob('latest.json'))
            store.discover({'url': URL, 'title': '新竹縣競選總部成立', 'source_id': 'web_newtalk',
                'publisher_id': 'newtalk', 'source_kind': 'media', 'source_grade': 'C'}, time.time(), enqueue_body=False)
            store.save_body(URL, {'body_status': 'read', 'content': '新竹縣候選人宣布競選總部成立，將展開選舉活動。' * 8,
                'page_date': (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()}, time.time())
            return {'status': 'completed', 'findings': [], 'search_count': 1}
    coordinator = Coordinator()
    backend = LocalNewsBackend(tmp_path, store, research_coordinator=coordinator)
    pipeline = AnalysisPipeline(tmp_path, mode='online', retrieval_backend=backend,
                                source_registry=SourceRegistry(config_path=tmp_path / 'missing.yaml'))
    context = pipeline.run(make_task()).analysis_context
    assert coordinator.calls == 1
    assert context['campaign_state']['retrieval_lead_count'] == 1
    assert context['local_knowledge']['automatic_research']['status'] == 'completed'
    assert len(list((tmp_path / 'cache').rglob('latest.json'))) == 1
    past = pipeline.run(make_task(), as_of='2026-01-01').analysis_context
    assert coordinator.calls == 1 and past['campaign_state']['retrieval_lead_count'] == 0
    assert past['evidence_summary']['automatic_research']['status'] == 'historical_replay'


def test_legacy_historical_jobs_do_not_perform_live_search(tmp_path):
    store, worker, model, search, _ = setup(tmp_path)
    job_id = store.schedule('past', {'jurisdiction': '高雄市', 'as_of': '2020-01-01', 'query': '派系'}, 1800)
    assert worker.tick(job_id)['status'] == 'historical_replay'
    assert not model.calls and not search.calls


def test_flybook_fallback_shows_research_findings_and_source(tmp_path):
    _, worker, _, _, job_id = setup(tmp_path)
    result = public_result(worker.tick(job_id))
    context = {'analysis_context': {'evidence_summary': {'automatic_research': result}}}
    request = ParsedRequest(intent='full_analysis', text='分析高雄', focus=ElectionFocus(jurisdiction='高雄市'))
    text = asyncio.run(DeterministicReportWriter().write(request, context))
    assert '已完成自动补查' in text and URL in text and QUOTE in text
    assert '仍待独立核实' in text
