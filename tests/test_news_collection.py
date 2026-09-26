"""Network-free behavioral tests of durable ingestion, provenance and bot wiring."""
import datetime as dt
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from bot.config import BotConfig
from bot.feishu_app import build_service
from runtime.campaign_delta import compare_campaign_snapshots
from runtime.campaign_event import CampaignEventResolver
from runtime.campaign_state import CampaignStateBuilder
from runtime.news_collector import NewsCollector
from runtime.news_retrieval import LocalNewsBackend, cutoff_time
from runtime.news_sources import SourceClient, SourceError, parse_links, retry_seconds
from runtime.news_store import NewsStore
from runtime.pipeline import AnalysisPipeline
from runtime.source_registry import SourceRegistry
from tests.fixtures.helpers import populate_full_repo, make_task

NOW = dt.datetime(2026, 9, 23, 4, tzinfo=dt.timezone.utc).timestamp()


def source(id='a', **kwargs):
    return dict(id=id, adapter='rss', url=f'https://{id}.test/feed',
                article_domains=[f'{id}.test'], publisher_id=id,
                source_kind='media', source_grade='C', interval_seconds=900, **kwargs)


def record(url='https://a.test/1', **kwargs):
    row = dict(url=url, title='高雄市候選人競選總部成立', source_id='a', source_kind='media',
               publisher_id='a', source_grade='C', source_name='a.test', independence_key='a',
               published_at='2026-09-23T09:00:00+08:00')
    row.update(kwargs)
    return row


def body(text='高雄市候選人競選總部成立，團隊將展開選舉活動。'):
    return dict(body_status='read', content=text, page_date='2026-09-23T09:00:00+08:00', content_sha256=text)


def setup(tmp_path, sources=None):
    (tmp_path / 'config').mkdir(exist_ok=True)
    (tmp_path / 'config/news_sources.yaml').write_text(yaml.safe_dump({'sources': sources or [source()]}))
    return NewsStore(tmp_path / 'news.sqlite3')


class Client:
    def __init__(self, fail=False, status=200):
        self.fail, self.status, self.calls = fail, status, []

    def get(self, url, conditional):
        self.calls.append((url, conditional))
        if self.fail and 'a.test' in url:
            raise SourceError('http_429', 7200)
        host = url.split('/')[2]
        data = f'<rss><channel><item><title>高雄市競選活動</title><link>https://{host}/1</link><pubDate>Wed, 23 Sep 2026 01:00:00 GMT</pubDate></item></channel></rss>'.encode()
        return data, {'ETag': 'v1'}, self.status


class Body:
    def __init__(self, status='read'):
        self.status, self.calls = status, []

    def fetch(self, url):
        self.calls.append(url)
        return body() if self.status == 'read' else {'body_status': self.status, 'retry_after_seconds': 3600}


def test_discovery_and_repeated_runs_are_idempotent(tmp_path):
    store = setup(tmp_path)
    clock = [NOW]
    client, fetcher = Client(), Body()
    collector = NewsCollector(tmp_path, store, client, fetcher, clock=lambda: clock[0], jitter=lambda: 0)
    collector.tick(2)
    clock[0] += 3
    collector.tick(2)
    assert len(fetcher.calls) == 1
    clock[0] += 900
    collector.tick(2)
    assert store.health()['article_count'] == 1
    assert len(fetcher.calls) == 1
    assert client.calls[-1][1]['etag'] == 'v1'


def test_429_is_persistent_and_does_not_block_other_source(tmp_path):
    store = setup(tmp_path, [source(), source('b')])
    clock = [NOW]
    client = Client(fail=True)
    collector = NewsCollector(tmp_path, store, client, Body(), clock=lambda: clock[0], jitter=lambda: 0)
    collector.tick(3)
    assert store.source('a')['failures'] == 1
    assert store.source('b')['last_success'] == NOW
    clock[0] += 300
    restarted = NewsCollector(tmp_path, store, client, Body(), clock=lambda: clock[0], jitter=lambda: 0)
    restarted.tick(2)
    assert sum('a.test' in u for u, _ in client.calls) == 1
    with store.connect() as db:
        assert db.execute("SELECT due FROM jobs WHERE kind='discover' AND key='a'").fetchone()[0] == NOW + 7200


def test_worker_lease_recovery_and_old_worker_cannot_finish(tmp_path):
    store = setup(tmp_path)
    store.discover(record(), NOW)
    job = store.claim('body', NOW, 10)
    assert store.claim('body', NOW + 1) is None
    replacement = store.claim('body', NOW + 11)
    assert replacement['token'] != job['token']
    store.finish(job)
    with store.connect() as db:
        assert db.execute("SELECT status FROM jobs WHERE kind='body'").fetchone()[0] == 'running'
    store.finish(replacement)


def test_host_spacing_survives_reopening_store(tmp_path):
    store = setup(tmp_path)
    assert store.reserve_host('a.test', NOW) is None
    assert NewsStore(store.path).reserve_host('a.test', NOW + 1) == NOW + 2


def test_versions_replay_and_reversion(tmp_path):
    store = setup(tmp_path)
    store.discover(record(), NOW)
    store.save_body(record()['url'], body('original'), NOW + 1)
    store.save_body(record()['url'], body('corrected'), NOW + 3)
    store.save_body(record()['url'], body('original'), NOW + 5)
    assert 'content' not in store.records_at(NOW)[0]
    assert store.records_at(NOW + 2)[0]['content'] == 'original'
    assert store.records_at(NOW + 4)[0]['content'] == 'corrected'
    assert store.records_at(NOW + 6)[0]['content'] == 'original'
    store.save_body(record()['url'], {'body_status': 'http_500'}, NOW + 7)
    assert store.article(record()['url'])['content'] == 'original'


def test_duplicate_feed_does_not_relabel_old_body(tmp_path):
    store = setup(tmp_path)
    store.discover(record(), NOW)
    store.save_body(record()['url'], body(), NOW + 1)
    store.discover(record(title='new headline', published_at='2026-09-24T01:00:00Z'), NOW + 2)
    assert store.article(record()['url'])['title'] != 'new headline'
    assert store.article(record()['url'])['published_at'] == record()['published_at']


def test_source_disable_stops_queued_discovery(tmp_path):
    store = setup(tmp_path)
    store.register_sources([source()], NOW)
    store.register_sources([], NOW)
    assert store.claim('discover', NOW + 1000) is None


def test_rss_atom_listing_and_sitemap(tmp_path):
    rss = b'<rss><channel><item><title>T</title><link>https://a.test/1?utm_source=x</link><pubDate>Wed, 23 Sep 2026 01:00:00 GMT</pubDate></item><item><link>https://evil.test/1</link></item></channel></rss>'
    rows = parse_links(rss, source())
    assert len(rows) == 1 and rows[0]['url'] == 'https://a.test/1'
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title><link rel="self" href="https://a.test/api"/><link href="https://a.test/2"/><updated>2026-09-23T01:00:00Z</updated></entry></feed>'
    rows = parse_links(atom, source())
    assert rows[0]['url'].endswith('/2') and not rows[0]['published_at']
    s = {**source(), 'adapter': 'listing', 'article_pattern': '/news/'}
    assert len(parse_links(b'<a href="/news/1">a</a><a href="/login">b</a>', s)) == 1
    sm = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://a.test/3</loc><lastmod>2026-09-23</lastmod></url></urlset>'
    assert not parse_links(sm, {**source(), 'adapter': 'sitemap'})[0]['published_at']


@pytest.mark.parametrize('xml', [b'<!DOCTYPE x><rss/>', b'<html>blocked</html>', b'<sitemapindex/>'])
def test_non_feed_or_unsafe_xml_fails_closed(xml):
    with pytest.raises(SourceError):
        parse_links(xml, source())


def test_retry_after_seconds_and_date():
    assert retry_seconds('7200') == 7200
    assert retry_seconds('Wed, 23 Sep 2026 05:00:00 GMT', NOW) == 3600
    assert retry_seconds('garbage') == 0


def test_import_does_not_trust_grades_content_or_verification(tmp_path):
    store = setup(tmp_path)
    collector = NewsCollector(tmp_path, store, Client(), Body(), clock=lambda: NOW)
    path = tmp_path / 'links.jsonl'
    path.write_text(json.dumps({**record(), 'source_grade': 'A', 'verification_status': 'verified', 'content': 'injected'}))
    assert collector.import_links(path) == 1
    row = store.article(record()['url'])
    assert row['source_grade'] == 'C' and row['verification_status'] == 'lead_only'
    assert 'content' not in row
    path.write_text(json.dumps(record(url='http://127.0.0.1/secret')))
    with pytest.raises(ValueError):
        collector.import_links(path)


def test_local_search_no_network_and_asof_cutoff(tmp_path):
    store = setup(tmp_path)
    store.discover(record(), NOW)
    store.save_body(record()['url'], body(), NOW + 1)
    store.save_body(record()['url'], body('高雄市候選人澄清新進展'), NOW + 100)
    backend = LocalNewsBackend(tmp_path, store)
    rows = backend.search('高雄市', jurisdiction='高雄市', as_of=dt.datetime.fromtimestamp(NOW + 10, dt.timezone.utc).isoformat())
    assert rows[0]['content'] == body()['content']
    assert backend.fetch(rows[0]['url'])['content'] == rows[0]['content']
    assert not backend.search('台北市', jurisdiction='台北市', as_of='2026-09-23')
    assert not backend.search('高雄市', jurisdiction='高雄市', as_of='2026-09-22')
    assert backend.metadata()['network_requests'] == 0


def test_unknown_publication_date_cannot_become_fresh_event(tmp_path):
    store = setup(tmp_path)
    store.discover(record(published_at=''), NOW)
    store.save_body(record()['url'], {**body(), 'page_date': ''}, NOW + 1)
    assert LocalNewsBackend(tmp_path, store).search('高雄市', jurisdiction='高雄市', as_of='2026-09-23') == []


def test_offline_pipeline_reads_local_articles(tmp_path):
    populate_full_repo(tmp_path)
    store = NewsStore(tmp_path / 'cache/news/news.sqlite3')
    store.discover(record(title='宜蘭縣候選人競選總部成立'), NOW)
    store.save_body(record()['url'], body('宜蘭縣候選人競選總部成立，團隊展開選舉活动。'), NOW + 1)
    backend = LocalNewsBackend(tmp_path, store)
    context = AnalysisPipeline(tmp_path, mode='offline', retrieval_backend=backend,
                               source_registry=SourceRegistry(config_path=tmp_path/'missing.yaml')).run(
                                   make_task(jurisdiction='宜兰县'), allow_online=False, as_of='2026-09-23').analysis_context
    assert context['campaign_state']['retrieval_lead_count'] == 1
    assert context['campaign_event_resolution']['stats']['event_count'] == 1
    assert context['evidence_summary']['retrieval']['network_requests'] == 0


@pytest.mark.parametrize('kind,grade', [('party','D'), ('candidate','D'), ('official','A'), ('other','E')])
def test_non_media_never_upgraded_by_media_resolver(kind, grade):
    row = {**record(source_kind=kind, source_grade=grade), **body()}
    assert CampaignEventResolver().extract([row], '高雄市')['events'] == []


def test_copied_bodies_do_not_count_as_independent():
    a = {**record(), **body()}
    b = {**record('https://b.test/1', publisher_id='b', independence_key='b'), **body()}
    event = CampaignEventResolver().extract([a, b], '高雄市')['events'][0]
    assert event['independent_source_count'] == 1
    assert event['verification_status'] == 'single_source_media'


def test_cna_wire_credit_does_not_count_as_independent():
    a = {**record(publisher_id='cna', independence_key='cna'), **body('中央社記者報導，高雄市競選總部成立，候選人宣布提出政策。')}
    b = {**record('https://b.test/1', publisher_id='b', independence_key='b'), **body('來源：中央社。高雄市競選總部成立，候選人表示這次將提出政策方案。')}
    events = CampaignEventResolver().extract([a, b], '高雄市')['events']
    assert all(e['verification_status'] != 'corroborated_media' for e in events)


def test_correction_requires_review_and_does_not_trigger():
    row = {**record(), **body('高雄市候選人澄清競選總部成立時間，更正先前報導。')}
    event = CampaignEventResolver().extract([row], '高雄市')['events'][0]
    assert event['verification_status'] == 'requires_review'
    assert event['structural_use'] == 'context_only'


def test_event_identity_and_revision_delta(tmp_path):
    store = setup(tmp_path)
    event = {'event_id': 'first', 'source_urls': ['https://a.test/1'], 'verification_status': 'single_source_media'}
    first = store.reconcile_events([event], '高雄:2026:mayor', NOW)[0]
    revised = store.reconcile_events([{**event, 'event_id': 'different', 'source_urls': ['https://a.test/1', 'https://b.test/2']}], '高雄:2026:mayor', NOW+1)[0]
    assert revised['event_id'] == first['event_id']
    delta = compare_campaign_snapshots({'event_ids': [first['event_id']], 'event_versions': {first['event_id']: first['event_version']}}, [], [revised], [])
    assert delta['new_event_ids'] == []
    assert delta['updated_event_ids'] == [first['event_id']]


def test_bot_local_is_default_even_offline(tmp_path):
    config = BotConfig(repo_root=tmp_path, skill_mode='offline', conversation_db=tmp_path/'bot.sqlite3')
    service = build_service(config)
    assert isinstance(service.skill_service.retrieval_backend, LocalNewsBackend)


def test_private_dns_is_rejected_before_any_request():
    client = SourceClient(['a.test'])
    client.reader.resolver = lambda *a, **k: [(0,0,0,'',('127.0.0.1',443))]
    client.reader.opener = lambda *a, **k: pytest.fail('network request escaped guard')
    with pytest.raises(SourceError, match='unsafe_source_url'):
        client.get('https://a.test/feed')


def test_empty_rss_links_do_not_resolve_to_feed_itself():
    data = b'<rss><channel><item><title/><link/><guid/><pubDate>Thu, 01 Jan 1970 08:00:00 +0800</pubDate></item></channel></rss>'
    assert parse_links(data, source()) == []


def test_304_keeps_articles_and_updates_health(tmp_path):
    store = setup(tmp_path)
    store.discover(record(), NOW - 10)
    collector = NewsCollector(tmp_path, store, Client(status=304), Body(), clock=lambda: NOW)
    collector.tick(1)
    assert store.source('a')['last_success'] == NOW
    assert store.health()['article_count'] == 1


def test_research_handoff_is_durable_and_offline_factory_disables_it(tmp_path):
    store = setup(tmp_path)
    backend = LocalNewsBackend(tmp_path, store)
    backend.search('高雄市提名', jurisdiction='高雄市', as_of='2026-09-23')
    backend.search('高雄市提名', jurisdiction='高雄市', as_of='2026-09-23')
    assert len(store.research_requests()) == 1
    assert NewsStore(store.path).research_requests()[0]['reason'] == 'coverage_gap'
    offline = LocalNewsBackend(tmp_path, store, queue_research=False)
    offline.search('台北市提名', jurisdiction='台北市')
    assert len(store.research_requests()) == 1


def test_body_retry_after_survives_next_run(tmp_path):
    store = setup(tmp_path)
    store.discover(record(), NOW)
    collector = NewsCollector(tmp_path, store, Client(), Body('http_429'), clock=lambda: NOW + 10, jitter=lambda: 0)
    # Discovery uses the same host, so postpone it while exercising body recovery.
    with store.connect() as db:
        db.execute("UPDATE jobs SET due=? WHERE kind='discover'", (NOW + 10000,))
    collector.tick(1)
    with store.connect() as db:
        row = db.execute("SELECT due,last_error FROM jobs WHERE kind='body'").fetchone()
        assert row['due'] == NOW + 3610
        assert row['last_error'] == 'http_429'


def test_candidate_names_not_ids_are_used_for_retrieval(tmp_path):
    store = setup(tmp_path)
    store.discover(record(title='王小明成立後援會'), NOW)
    store.save_body(record()['url'], body('王小明成立後援會，宣布競選工作安排。'), NOW + 1)
    backend = LocalNewsBackend(tmp_path, store)
    builder = CampaignStateBuilder(tmp_path, retrieval_backend=backend, mode='offline')
    rows, _ = builder.retrieve_current_leads('高雄市', 2026, False, '2026-09-23',
            [{'candidate_id': 'person-123', 'candidate_name': '王小明'}])
    assert len(rows) == 1


def test_changing_source_endpoint_discards_old_etag(tmp_path):
    store = setup(tmp_path)
    store.register_sources([source()], NOW)
    store.record_run('a', NOW, 'ok', headers={'ETag': 'old'})
    store.register_sources([{**source(), 'url': 'https://a.test/new-feed'}], NOW + 1)
    assert store.source('a')['etag'] is None
    assert store.source('a')['last_success'] is None


def test_alternate_active_discovery_keeps_body_task_alive(tmp_path):
    store = setup(tmp_path, [{**source('b'), 'article_domains': ['a.test']}])
    store.discover(record(), NOW)
    store.discover(record(source_id='b'), NOW)
    fetcher = Body()
    collector = NewsCollector(tmp_path, store, Client(), fetcher, clock=lambda: NOW + 10)
    with store.connect() as db:
        db.execute("UPDATE jobs SET due=? WHERE kind='discover'", (NOW + 1000,))
    collector.tick(1)
    assert len(fetcher.calls) == 1
