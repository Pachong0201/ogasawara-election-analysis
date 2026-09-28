"""Official Legislative Yuan current-member adapter.

Reads the Legislative Yuan current-member list and individual profile pages.
Only geographically mappable current legislators are returned for county L3
knowledge. Party-list and indigenous-at-large seats remain unassigned instead
of being forced into a county.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .county_knowledge import COUNTIES
from .models import utc_now_iso


LY_CURRENT_MEMBERS_URL = "https://www.ly.gov.tw/Pages/List.aspx?nodeid=109"


def _download_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ogasawara-election-analysis/1.4",
            "Accept": "text/html,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    if not raw:
        raise RuntimeError(f"empty Legislative Yuan response: {url}")
    return raw.decode("utf-8", errors="replace")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


class _CurrentMemberLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.section = ""
        self._href: Optional[str] = None
        self._image_labels: List[str] = []
        self._visible_text: List[str] = []
        self._recent_text: List[str] = []
        self.links: List[Tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)
        if tag.lower() == "a":
            self._href = attrs_dict.get("href")
            self._image_labels = []
            self._visible_text = []
            return
        if tag.lower() == "img" and self._href is not None:
            label = _clean(attrs_dict.get("alt") or attrs_dict.get("title") or "")
            if label:
                self._image_labels.append(label)

    def handle_data(self, data: str) -> None:
        text = _clean(data)
        if not text:
            return
        self._recent_text.append(text)
        self._recent_text = self._recent_text[-8:]
        joined = "".join(self._recent_text).replace(" ", "")
        if "離職立法委員名單" in joined:
            self.section = "left"
        elif "第11屆立法委員名單" in joined:
            self.section = "current"
        if self._href is not None:
            self._visible_text.append(text)

    @staticmethod
    def _member_name(visible_text: Sequence[str], image_labels: Sequence[str]) -> str:
        """Choose the member label without concatenating party-badge alt text.

        The live roster anchor contains a portrait alt, a party-logo alt and a
        visible name.  Treating every fragment as anchor text produced values
        such as ``吳秉叡 民主進步黨徽章 吳秉叡``.  Prefer visible name text and
        only fall back to portrait alt text for image-only markup.
        """
        ignored = {
            "中國國民黨", "民主進步黨", "台灣民眾黨", "臺灣民眾黨",
            "時代力量", "台灣基進", "臺灣基進", "無黨籍", "無",
        }
        for value in list(visible_text) + list(image_labels):
            candidate = _clean(value)
            candidate = re.sub(r"(?:委員)?照片$", "", candidate).strip()
            if not candidate or "徽章" in candidate or candidate in ignored:
                continue
            if "立法委員名單" in candidate:
                continue
            return candidate
        return ""

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = self._member_name(self._visible_text, self._image_labels)
        href = self._href
        self._href = None
        self._image_labels = []
        self._visible_text = []
        if self.section != "current" or not text or "nodeid=" not in href:
            return
        self.links.append((href, text))


class _TextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        text = _clean(data)
        if text:
            self.parts.append(text)


def _roc_date(value: str) -> str:
    match = re.search(r"(\d{2,3})年\s*(\d{1,2})月\s*(\d{1,2})日", value)
    if not match:
        return ""
    year, month, day = (int(x) for x in match.groups())
    return f"{year + 1911:04d}-{month:02d}-{day:02d}"


def _field(parts: List[str], label: str) -> str:
    for index, part in enumerate(parts):
        compact = part.replace(" ", "")
        if compact.startswith(label):
            value = part.split("：", 1)[1] if "：" in part else ""
            if value.strip():
                return _clean(value)
            if index + 1 < len(parts):
                return _clean(parts[index + 1])
    return ""


def county_from_constituency(value: str) -> str:
    normalized = _clean(value).replace("臺", "台")
    for county in COUNTIES:
        if normalized.startswith(county.replace("臺", "台")):
            return county
    return ""


class LYCurrentLegislatorAdapter:
    source_id = "legislative_yuan_current_members"
    source_grade = "A"

    def __init__(self, page_url: str = LY_CURRENT_MEMBERS_URL, fetcher: Any = None):
        self.page_url = page_url
        self.fetcher = fetcher or _download_text

    def member_links(self) -> List[Tuple[str, str]]:
        html = self.fetcher(self.page_url)
        parser = _CurrentMemberLinks()
        parser.feed(html)
        output: List[Tuple[str, str]] = []
        seen = set()
        for href, name in parser.links:
            url = urllib.parse.urljoin(self.page_url, href)
            if url == self.page_url:
                continue
            key = (url, name)
            if key in seen:
                continue
            seen.add(key)
            output.append(key)
        return output

    def _profile(self, name: str, url: str) -> Optional[Dict[str, Any]]:
        html = self.fetcher(url)
        parser = _TextCollector()
        parser.feed(html)
        parts = parser.parts
        term = _field(parts, "屆別：") or _field(parts, "屆別")
        if "11" not in term:
            return None
        party = _field(parts, "黨籍：") or _field(parts, "黨籍")
        constituency = _field(parts, "選區：") or _field(parts, "選區")
        onboard_raw = _field(parts, "到職日期：") or _field(parts, "到職日期")
        county = county_from_constituency(constituency)
        now = utc_now_iso()
        return {
            "member_id": "ly11-" + hashlib.sha1(
                f"{name}|{constituency}|{url}".encode("utf-8")
            ).hexdigest()[:16],
            "name": name,
            "party": party,
            "constituency": constituency,
            "county": county,
            "onboard_date": _roc_date(onboard_raw),
            "current_status": "current" if county else "current_unassigned",
            "source_id": self.source_id,
            "source_grade": self.source_grade,
            "source_reference": url,
            "source_name": "立法院",
            "retrieved_at": now,
            "last_verified_at": now,
        }

    def fetch_all(self) -> Dict[str, Any]:
        records: List[Dict[str, Any]] = []
        failures: List[Dict[str, str]] = []
        for url, name in self.member_links():
            try:
                record = self._profile(name, url)
                if record:
                    records.append(record)
            except Exception as exc:
                failures.append({"name": name, "url": url, "error": f"{type(exc).__name__}: {exc}"})
        geographic = [row for row in records if row.get("county")]
        unassigned = [row for row in records if not row.get("county")]
        return {
            "records": geographic,
            "unassigned": unassigned,
            "failure_count": len(failures),
            "failures": failures,
            "source_url": self.page_url,
        }
