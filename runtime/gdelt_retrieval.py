"""Public GDELT DOC 2.0 news discovery for the Feishu bot.

ArticleList supplies headlines, URLs and first-seen timestamps. Selected public
article pages are then read by ArticleBodyFetcher. Prose remains unverified.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .models import utc_now_iso
from .source_registry import OfflineRetrievalError, RetrievalBackend
from .article_body import ArticleBodyFetcher


ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
# Chinese names from the router and their English equivalents used by GDELT's
# cross-language index. The longest jurisdiction token wins (e.g. 新竹市/新竹縣).
COUNTIES = {
    "臺北市": "Taipei", "台北市": "Taipei", "新北市": "New Taipei",
    "桃園市": "Taoyuan", "桃园市": "Taoyuan", "臺中市": "Taichung", "台中市": "Taichung",
    "臺南市": "Tainan", "台南市": "Tainan", "高雄市": "Kaohsiung",
    "基隆市": "Keelung", "新竹市": "Hsinchu City", "新竹縣": "Hsinchu County",
    "新竹县": "Hsinchu County", "嘉義市": "Chiayi City", "嘉义市": "Chiayi City",
    "嘉義縣": "Chiayi County", "嘉义县": "Chiayi County", "苗栗縣": "Miaoli", "苗栗县": "Miaoli",
    "彰化縣": "Changhua", "彰化县": "Changhua", "南投縣": "Nantou", "南投县": "Nantou",
    "雲林縣": "Yunlin", "云林县": "Yunlin", "屏東縣": "Pingtung", "屏东县": "Pingtung",
    "宜蘭縣": "Yilan", "宜兰县": "Yilan", "花蓮縣": "Hualien", "花莲县": "Hualien",
    "臺東縣": "Taitung", "台东县": "Taitung", "澎湖縣": "Penghu", "澎湖县": "Penghu",
    "金門縣": "Kinmen", "金门县": "Kinmen", "連江縣": "Matsu", "连江县": "Matsu",
}


def _county(query: str, jurisdiction: str) -> str:
    text = jurisdiction or query
    matches = [name for name in COUNTIES if name in text]
    return COUNTIES[max(matches, key=len)] if matches else ""


def _topic(query: str, purpose: str) -> str:
    if purpose == "local_knowledge":
        return "(election OR mayor)"
    if any(term in query for term in ("民調", "民调")):
        return "(poll OR survey)"
    if any(term in query for term in ("政策", "爭議", "争议", "議題", "议题")):
        return "(election OR mayor OR campaign)"
    if any(term in query for term in ("合作", "競選", "竞选", "支持", "組織", "组织")):
        return "(campaign OR election OR mayor)"
    return "(mayor OR election OR campaign)"


def _canonical_url(raw: Any) -> str:
    try:
        parsed = urlparse(str(raw or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        # Preserve article identifiers in query strings; strip common trackers.
        pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
        return urlunparse((parsed.scheme, parsed.hostname.lower() + (f":{parsed.port}" if parsed.port else ""),
                           parsed.path or "/", "", urlencode(sorted(pairs)), ""))
    except ValueError:
        return ""


def _first_seen(raw: Any) -> str:
    try:
        return dt.datetime.strptime(str(raw), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).isoformat()
    except ValueError:
        return ""


class GDELTNewsBackend(RetrievalBackend):
    """Live news discovery and bounded public body reading, without promotion."""

    def __init__(self, timeout: float = 6.0, max_records: int = 10,
                 opener: Optional[Callable[..., Any]] = None,
                 body_fetcher: Optional[ArticleBodyFetcher] = None,
                 max_body_fetches: int = 6):
        self.timeout = max(1.0, min(float(timeout), 15.0))
        self.max_records = max(1, min(int(max_records), 25))
        self.opener = opener or urlopen
        self.body_fetcher = body_fetcher or ArticleBodyFetcher()
        self.max_body_fetches = max(0, min(int(max_body_fetches), 12))
        self._cooldown_until = 0.0
        self._seen: Dict[str, Dict[str, Any]] = {}
        self._last_enrichment: Dict[str, int] = {"attempted": 0, "read": 0}

    def search(self, query: str, **kwargs: Any) -> List[Dict[str, Any]]:
        if time.monotonic() < self._cooldown_until:
            raise OfflineRetrievalError("GDELT news retrieval temporarily unavailable")
        county = _county(query, str(kwargs.get("jurisdiction") or ""))
        if not county:
            return []  # No jurisdiction: refuse to search unrelated news.
        days = max(1, min(int(kwargs.get("recency_days") or 30), 90))
        gdelt_query = f'"{county}" {_topic(query, str(kwargs.get("purpose") or ""))}'
        params = {
            "query": gdelt_query, "mode": "artlist", "format": "json",
            "sort": "datedesc", "timespan": f"{days}d",
            "maxrecords": self.max_records,
        }
        request = Request(
            ENDPOINT + "?" + urlencode(params),
            headers={"User-Agent": "ogasawara-election-analysis/1.4 (+public-news-discovery)", "Accept": "application/json"},
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError("GDELT response too large")
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get("articles", []), list):
                raise ValueError("invalid GDELT ArticleList response")
        except (OSError, ValueError, TypeError) as exc:
            self._cooldown_until = time.monotonic() + 60
            raise OfflineRetrievalError(f"GDELT news retrieval unavailable: {type(exc).__name__}") from exc

        records: List[Dict[str, Any]] = []
        for row in payload.get("articles", [])[:self.max_records]:
            if not isinstance(row, dict):
                continue
            url = _canonical_url(row.get("url"))
            title = str(row.get("title") or "").strip()[:300]
            if not url or not title:
                continue
            domain = urlparse(url).hostname or ""
            record = {
                "query": query, "title": title, "summary": "",
                "url": url, "source_id": "gdelt_doc_news", "source_name": domain,
                "independence_key": domain, "source_grade": "E",
                "verification_status": "lead_only", "first_seen_at": _first_seen(row.get("seendate")),
                "retrieved_at": utc_now_iso(),
                "evidence": "GDELT ArticleList headline and URL only; article content unverified",
            }
            self._seen[url] = record
            records.append(record)
        return records

    def fetch(self, url: str) -> Any:
        # Only search-returned URLs can reach the public body reader.
        record = self._seen.get(url)
        if record is None:
            return None
        if "body_status" not in record:
            try:
                record.update(self.body_fetcher.fetch(url))
            except Exception:
                record["body_status"] = "fetch_failed"
            if record.get("body_status") == "read":
                record["verification_status"] = "body_read_unverified"
                record["evidence"] = "Publisher page body extracted; factual claims not independently verified"
        return dict(record)

    def enrich(self, leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Read a bounded, cross-topic sample after searches are deduplicated."""
        enriched: List[Dict[str, Any]] = []
        body_hashes: Dict[str, str] = {}
        attempted = read = 0
        for lead in leads:
            url = str(lead.get("url") or "")
            if not self.body_fetcher.supports(url):
                row = {**lead, "body_status": "unsupported_domain"}
            elif attempted < self.max_body_fetches and url in self._seen:
                attempted += 1
                row = {**(self.fetch(url) or dict(lead)), "query": lead.get("query", "")}
                if row.get("body_status") == "read":
                    read += 1
                    digest = str(row.get("content_sha256") or "")
                    if digest in body_hashes:
                        row["duplicate_of"] = body_hashes[digest]
                    else:
                        body_hashes[digest] = url
            else:
                row = {**lead, "body_status": "not_fetched_budget"}
            enriched.append(row)
        self._last_enrichment = {"attempted": attempted, "read": read}
        return enriched

    def metadata(self) -> Dict[str, Any]:
        return {"backend": "gdelt_doc_news", "scope": "recent_news_and_public_bodies",
                "lead_only": True, "available": time.monotonic() >= self._cooldown_until,
                "body_fetch": dict(self._last_enrichment)}
