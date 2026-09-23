"""Read publicly accessible article bodies with bounded, attributable fetches.

Only selected publisher domains are fetched. This reader does not authenticate,
follow redirects, bypass paywalls, or treat extracted prose as verified facts.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
import datetime as dt
from html.parser import HTMLParser
from typing import Any, Callable, Dict, Iterable, Optional
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser


USER_AGENT = "OgasawaraElectionBot/0.2 (+https://github.com/Pachong0201/ogasawara-election-analysis)"
DEFAULT_DOMAINS = frozenset({
    "cna.com.tw", "focustaiwan.tw", "udn.com", "ltn.com.tw", "newtalk.tw",
    "storm.mg", "ettoday.net", "ebc.net.tw", "tvbs.com.tw", "pts.org.tw",
    "chinatimes.com", "setn.com", "nownews.com", "rti.org.tw", "ctwant.com",
    "mirrormedia.mg", "rwnews.tw", "upmedia.mg", "taiwannews.com.tw", "gov.tw",
})
MAX_HTML_BYTES = 2_000_000
MAX_BODY_CHARS = 25_000


class _PublishedMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.date = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "meta" or self.date:
            return
        fields = dict(attrs)
        key = (fields.get("property") or fields.get("name") or "").lower()
        value = fields.get("content") or ""
        if key in {"article:published_time", "datepublished", "pubdate", "publish_date"}:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:+Z.-]+)?", value.strip()):
                try:
                    dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
                    self.date = value.strip()
                except ValueError:
                    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str) -> None:
        return None


def _valid_host(url: str, allowed_domains: Iterable[str]) -> str:
    try:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            return ""
    except ValueError:
        return ""
    host = parsed.hostname.lower().rstrip(".")
    if not any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
        return ""
    return host


class ArticleBodyFetcher:
    """Public HTML reader with robots checks, domain isolation and size caps."""

    def __init__(self, allowed_domains: Optional[Iterable[str]] = None, timeout: float = 5.0,
                 opener: Optional[Callable[..., Any]] = None,
                 resolver: Optional[Callable[..., Any]] = None):
        self.allowed_domains = frozenset(
            str(domain).lower().strip().lstrip(".")
            for domain in (allowed_domains if allowed_domains is not None else DEFAULT_DOMAINS)
            if str(domain).strip()
        )
        self.timeout = max(1.0, min(float(timeout), 10.0))
        self.opener = opener or build_opener(_NoRedirect()).open
        self.resolver = resolver or socket.getaddrinfo
        self._robots: Dict[str, tuple[float, Optional[RobotFileParser]]] = {}
        self._next_allowed: Dict[str, float] = {}

    def supports(self, url: str) -> bool:
        return bool(_valid_host(url, self.allowed_domains))

    def _public_dns(self, host: str) -> bool:
        try:
            answers = self.resolver(host, 443, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(item[4][0]) for item in answers]
            return bool(addresses) and all(address.is_global for address in addresses)
        except (OSError, ValueError, IndexError):
            return False

    def _request(self, url: str, accept: str, cap: int) -> tuple[bytes, Any]:
        request = Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "identity",
        })
        with self.opener(request, timeout=self.timeout) as response:
            if int(getattr(response, "status", 200)) != 200:
                raise ValueError("non-200 response")
            headers = getattr(response, "headers", {})
            if headers.get("Content-Length") and int(headers.get("Content-Length")) > cap:
                raise ValueError("response too large")
            body = response.read(cap + 1)
            if len(body) > cap:
                raise ValueError("response too large")
            return body, headers

    def _can_fetch(self, host: str, url: str) -> str:
        now = time.monotonic()
        entry = self._robots.get(host)
        if entry is None or now > entry[0]:
            robots_url = f"https://{host}/robots.txt"
            try:
                content, _ = self._request(robots_url, "text/plain", 200_000)
                parser = RobotFileParser()
                parser.parse(content.decode("utf-8", errors="replace").splitlines())
            except HTTPError as exc:
                if exc.code not in (404, 410):
                    self._robots[host] = (now + 60, None)
                    return "robots_unavailable"
                parser = RobotFileParser()
                parser.parse([])
            except (OSError, ValueError):
                self._robots[host] = (now + 60, None)
                return "robots_unavailable"
            self._robots[host] = (now + 900, parser)
        else:
            parser = entry[1]
        if parser is None:
            return "robots_unavailable"
        if not parser.can_fetch(USER_AGENT, url):
            return "robots_denied"
        if now < self._next_allowed.get(host, 0):
            return "crawl_delay"
        delay = parser.crawl_delay(USER_AGENT) or 0
        if delay:
            self._next_allowed[host] = now + min(int(delay), 60)
        return ""

    def fetch(self, url: str) -> Dict[str, Any]:
        host = _valid_host(url, self.allowed_domains)
        if not host:
            return {"body_status": "unsupported_domain"}
        if not self._public_dns(host):
            return {"body_status": "unsafe_dns"}
        reason = self._can_fetch(host, url)
        if reason:
            return {"body_status": reason}
        current_url = url
        for redirects in range(3):
            try:
                html, headers = self._request(current_url, "text/html,application/xhtml+xml", MAX_HTML_BYTES)
                break
            except HTTPError as exc:
                if exc.code not in (301, 302, 303, 307, 308):
                    return {"body_status": f"http_{exc.code}"}
                if redirects == 2:
                    return {"body_status": "redirect_limit"}
                target = urljoin(current_url, str(exc.headers.get("Location") or ""))
                next_host = _valid_host(target, self.allowed_domains)
                if not next_host or not self._public_dns(next_host):
                    return {"body_status": "redirect_blocked"}
                if re.search(r"/(?:login|signin|subscribe|paywall)(?:/|$)", urlparse(target).path, re.I):
                    return {"body_status": "access_restricted"}
                reason = self._can_fetch(next_host, target)
                if reason:
                    return {"body_status": reason}
                current_url = target
            except (OSError, ValueError):
                return {"body_status": "fetch_failed"}
        content_type = str(headers.get("Content-Type", "text/html")).lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            return {"body_status": "not_html"}
        try:
            import trafilatura
            raw = trafilatura.extract(
                html, url=current_url, output_format="json", with_metadata=True,
                include_comments=False, include_tables=True,
            )
            parsed = json.loads(raw) if raw else {}
        except Exception:
            return {"body_status": "extraction_failed"}
        content = str(parsed.get("text") or "").strip()
        if len(content) < 160:
            return {"body_status": "body_too_short"}
        truncated = len(content) > MAX_BODY_CHARS
        content = content[:MAX_BODY_CHARS]
        published = _PublishedMeta()
        published.feed(html.decode("utf-8", errors="replace"))
        return {
            "body_status": "read", "content": content,
            "content_truncated": truncated,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "page_title": str(parsed.get("title") or "").strip()[:300],
            "page_date": published.date,
            "resolved_url": current_url,
        }
