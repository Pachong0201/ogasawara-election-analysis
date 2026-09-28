import io
import json
import unittest
from urllib.error import HTTPError

from runtime.article_body import ArticleBodyFetcher
from runtime.gdelt_retrieval import GDELTNewsBackend
from runtime.campaign_state import CampaignStateBuilder
from runtime.pipeline import AnalysisPipeline
from runtime.source_registry import SourceRegistry
from bot.report_writer import _analysis_payload
from tests.fixtures.helpers import make_task, populate_full_repo
from pathlib import Path
from tempfile import TemporaryDirectory


PARAGRAPH = (
    '高雄市長選戰的最新報導引述地方人士說明競選總部近期的組織調整，'
    '文中逐一列出活動時間、受訪者與聲明內容。'
) * 5
HTML = (f'<html><head><title>高雄選戰進展</title>'
        '<meta property="article:published_time" content="2026-09-23T09:00:00+08:00"></head>'
        f'<body><nav>導覽頁籤</nav><article><h1>高雄選戰進展</h1>'
        f'<p>{PARAGRAPH}</p><p>{PARAGRAPH}</p></article></body></html>').encode()


class Response(io.BytesIO):
    def __init__(self, data, content_type):
        super().__init__(data)
        self.headers = {'Content-Type': content_type}
        self.status = 200
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.close()


def public_dns(host, port, type):
    return [(2, 1, 6, '', ('93.184.216.34', 443))]


class TestArticleBodyFetcher(unittest.TestCase):
    def test_reads_article_body_and_page_date(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            if request.full_url.endswith('/robots.txt'):
                return Response(b'User-agent: *\nAllow: /\n', 'text/plain')
            return Response(HTML, 'text/html; charset=utf-8')
        fetcher = ArticleBodyFetcher(allowed_domains={'news.test'}, opener=opener, resolver=public_dns)
        result = fetcher.fetch('https://news.test/story?id=1')
        self.assertEqual(result['body_status'], 'read')
        self.assertIn('競選總部', result['content'])
        self.assertNotIn('導覽頁籤', result['content'])
        self.assertTrue(result['content_sha256'])
        self.assertEqual(result['page_date'], '2026-09-23T09:00:00+08:00')
        self.assertEqual(len(calls), 2)
        fetcher.fetch('https://news.test/story?id=2')
        self.assertEqual(len([url for url in calls if url.endswith('/robots.txt')]), 1)

    def test_robots_disallow_prevents_article_request(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            return Response(b'User-agent: *\nDisallow: /story\n', 'text/plain')
        fetcher = ArticleBodyFetcher(allowed_domains={'news.test'}, opener=opener, resolver=public_dns)
        self.assertEqual(fetcher.fetch('https://news.test/story')['body_status'], 'robots_denied')
        self.assertEqual(calls, ['https://news.test/robots.txt'])

    def test_rejects_unsafe_urls_and_private_dns(self):
        fetcher = ArticleBodyFetcher(allowed_domains={'news.test'}, opener=lambda *_a, **_k: self.fail('network called'),
                                     resolver=lambda *_a, **_k: [(2, 1, 6, '', ('127.0.0.1', 443))])
        for url in ('http://news.test/story', 'https://news.test:9999/story',
                    'https://news.test.evil.com/story', 'https://user@news.test/story',
                    'https://news.test:bad/story'):
            self.assertEqual(fetcher.fetch(url)['body_status'], 'unsupported_domain')
        self.assertEqual(fetcher.fetch('https://news.test/story')['body_status'], 'unsafe_dns')

    def test_paywall_and_redirect_do_not_trigger_fallback(self):
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return Response(b'User-agent: *\nAllow: /\n', 'text/plain')
            raise HTTPError(request.full_url, 403, 'Forbidden', {}, None)
        fetcher = ArticleBodyFetcher(allowed_domains={'news.test'}, opener=opener, resolver=public_dns)
        self.assertEqual(fetcher.fetch('https://news.test/story')['body_status'], 'http_403')

    def test_safe_redirect_reads_page_and_blocks_login(self):
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return Response(b'User-agent: *\nAllow: /\n', 'text/plain')
            if request.full_url.endswith('/old'):
                raise HTTPError(request.full_url, 302, 'Moved', {'Location': '/story'}, None)
            return Response(HTML, 'text/html')
        fetcher = ArticleBodyFetcher(allowed_domains={'news.test'}, opener=opener, resolver=public_dns)
        self.assertEqual(fetcher.fetch('https://news.test/old')['resolved_url'], 'https://news.test/story')
        def login_opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return Response(b'User-agent: *\nAllow: /\n', 'text/plain')
            raise HTTPError(request.full_url, 302, 'Moved', {'Location': '/login'}, None)
        fetcher = ArticleBodyFetcher(allowed_domains={'news.test'}, opener=login_opener, resolver=public_dns)
        self.assertEqual(fetcher.fetch('https://news.test/old')['body_status'], 'access_restricted')

    def test_short_or_non_html_body_is_not_marked_read(self):
        for data, content_type, status in [
            (b'<html><article>Subscribe to read</article></html>', 'text/html', 'body_too_short'),
            (b'%PDF', 'application/pdf', 'not_html'),
        ]:
            def opener(request, timeout):
                if request.full_url.endswith('/robots.txt'):
                    return Response(b'User-agent: *\nAllow: /\n', 'text/plain')
                return Response(data, content_type)
            fetcher = ArticleBodyFetcher(allowed_domains={'news.test'}, opener=opener, resolver=public_dns)
            self.assertEqual(fetcher.fetch('https://news.test/story')['body_status'], status)

    def test_gdelt_results_are_enriched_but_not_verified(self):
        def gdelt_opener(request, timeout):
            return Response(json.dumps({'articles': [
                {'url': 'https://news.test/a', 'title': 'A', 'seendate': '20260923T000000Z'},
                {'url': 'https://news.test/b', 'title': 'B', 'seendate': '20260923T000000Z'},
            ]}).encode(), 'application/json')
        def article_opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return Response(b'User-agent: *\nAllow: /\n', 'text/plain')
            return Response(HTML, 'text/html')
        fetcher = ArticleBodyFetcher(allowed_domains={'news.test'}, opener=article_opener, resolver=public_dns)
        backend = GDELTNewsBackend(opener=gdelt_opener, body_fetcher=fetcher, max_body_fetches=2)
        with TemporaryDirectory() as tmp:
            leads, warnings = CampaignStateBuilder(Path(tmp), retrieval_backend=backend).retrieve_current_leads('高雄市', 2026, True)
        self.assertFalse(warnings)
        self.assertEqual(len(leads), 2)
        self.assertEqual(backend.metadata()['body_fetch'], {'attempted': 2, 'read': 2})
        self.assertTrue(all(lead['verification_status'] == 'lead_only' for lead in leads))
        self.assertTrue(all(lead['body_status'] == 'read' for lead in leads))
        self.assertTrue(all(lead.get('reported_verification_status') == 'body_read_unverified' for lead in leads))
        self.assertTrue(all(lead['source_grade'] == 'E' for lead in leads))
        self.assertEqual(leads[1]['duplicate_of'], leads[0]['url'])
        self.assertIn('競選總部', leads[0]['content'])

    def test_full_body_reaches_analysis_and_writer_contract(self):
        def gdelt_opener(request, timeout):
            return Response(json.dumps({'articles': [
                {'url': 'https://news.test/a', 'title': 'A', 'seendate': '20260923T000000Z'},
            ]}).encode(), 'application/json')
        def article_opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return Response(b'User-agent: *\nAllow: /\n', 'text/plain')
            return Response(HTML, 'text/html')
        fetcher = ArticleBodyFetcher(allowed_domains={'news.test'}, opener=article_opener, resolver=public_dns)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            populate_full_repo(root)
            backend = GDELTNewsBackend(opener=gdelt_opener, body_fetcher=fetcher)
            registry = SourceRegistry(config_path=root / 'config' / 'no-adapters.yaml')
            context = AnalysisPipeline(root, mode='online', retrieval_backend=backend,
                                       source_registry=registry).run(make_task(jurisdiction='高雄市'), allow_online=True).to_dict()
        raw_lead = context['analysis_context']['campaign_state']['retrieval_leads'][0]
        self.assertEqual(raw_lead['body_status'], 'read')
        self.assertIn(PARAGRAPH, raw_lead['content'])

        writer_payload = _analysis_payload(context)
        lead = writer_payload['campaign_state']['retrieval_leads'][0]
        self.assertEqual(lead['body_status'], 'read')
        self.assertNotIn('content', lead)
        self.assertEqual(writer_payload['campaign_state']['verified_retrieval_lead_count'], 0)


if __name__ == '__main__':
    unittest.main()
