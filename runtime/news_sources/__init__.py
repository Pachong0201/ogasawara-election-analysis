"""Bounded RSS/Atom, HTML listing and sitemap adapters with a shared safe client."""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request

from ..article_body import ArticleBodyFetcher, USER_AGENT, _valid_host
from ..news_utils import _canonical_url, retry_seconds


class SourceError(RuntimeError):
    def __init__(self, reason, retry_after=0):
        super().__init__(reason)
        self.retry_after = retry_after


def published(value):
    if not value:
        return ''
    try:
        date = dt.datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        try:
            date = parsedate_to_datetime(value.strip())
        except (TypeError, ValueError, OverflowError):
            return ''
    if date.tzinfo is None:
        date = date.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return date.isoformat()


class SourceClient:
    def __init__(self, domains, timeout=8):
        self.reader = ArticleBodyFetcher(allowed_domains=domains, timeout=timeout)

    def get(self, url, conditional=None):
        host = _valid_host(url, self.reader.allowed_domains)
        if not host or not self.reader._public_dns(host):
            raise SourceError('unsafe_source_url')
        reason = self.reader._can_fetch(host, url)
        if reason:
            raise SourceError(reason, 60)
        headers = {'User-Agent': USER_AGENT, 'Accept': 'application/xml,text/xml,application/rss+xml,text/html', 'Accept-Encoding': 'identity'}
        if conditional:
            if conditional.get('etag'):
                headers['If-None-Match'] = conditional['etag']
            if conditional.get('modified'):
                headers['If-Modified-Since'] = conditional['modified']
        try:
            # No automatic redirects: every configured endpoint must be explicit.
            with self.reader.opener(Request(url, headers=headers), timeout=self.reader.timeout) as response:
                data = response.read(2_000_001)
                if len(data) > 2_000_000:
                    raise SourceError('source_too_large')
                return data, dict(response.headers), 200
        except HTTPError as exc:
            if exc.code == 304:
                return b'', dict(exc.headers), 304
            raise SourceError(f'http_{exc.code}', retry_seconds(exc.headers.get('Retry-After'))) from exc
        except OSError as exc:
            raise SourceError(type(exc).__name__) from exc


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href = dict(attrs).get('href')
            self.parts = []

    def handle_data(self, data):
        if self.href:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            self.links.append((self.href, ''.join(self.parts).strip()))
            self.href = None


def parse_links(data, source):
    """Return link records; sitemap lastmod is never a publication date."""
    rows = []
    if source['adapter'] == 'listing':
        parser = Links()
        parser.feed(data.decode(source.get('encoding', 'utf-8'), errors='replace'))
        rows = [{'url': urljoin(source['url'], u), 'title': t} for u, t in parser.links]
    else:
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise SourceError('unsafe_xml')
        root = ET.fromstring(data)
        tag = lambda e: e.tag.rsplit('}', 1)[-1]
        if tag(root) == 'sitemapindex':
            raise SourceError('configure_leaf_sitemap')
        if tag(root) not in ('rss', 'RDF', 'feed', 'urlset'):
            raise SourceError('unexpected_document')
        for item in root.iter():
            if tag(item) not in ('item', 'entry', 'url'):
                continue
            fields = {}
            for child in item:
                key = tag(child)
                if key == 'link' and child.attrib.get('href'):
                    if child.attrib.get('rel', 'alternate') == 'alternate':
                        fields['link'] = child.attrib['href']
                else:
                    fields[key] = ''.join(child.itertext()).strip()
            url = fields.get('link') or fields.get('loc') or fields.get('guid', '')
            if not url.strip():
                continue
            rows.append({'url': urljoin(source['url'], url), 'title': fields.get('title', ''),
                         'published_at': published(fields.get('pubDate') or fields.get('published') or fields.get('date')),
                         'modified_at': published(fields.get('updated') or fields.get('lastmod'))})
    out, seen = [], set()
    for row in rows:
        url = _canonical_url(row['url'])
        if not _valid_host(url, source['article_domains']) or url in seen:
            continue
        if source.get('article_pattern') and not re.search(source['article_pattern'], url):
            continue
        seen.add(url)
        out.append({**row, 'url': url, 'source_id': source['id'],
                    'source_name': urlparse(url).hostname, 'publisher_id': source['publisher_id'],
                    'independence_key': source['publisher_id'], 'source_grade': source['source_grade'],
                    'source_kind': source['source_kind'], 'jurisdictions': source.get('jurisdictions', [])})
        if len(out) >= source.get('max_links', 100):
            break
    return out
