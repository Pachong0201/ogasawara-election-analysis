"""TVBS Poll Center adapter.

This adapter ingests polls from TVBS's own poll-center index and original PDF
reports. It does not use downstream news rewrites as the data source.

Poll records remain calibration evidence only. This module never compares
different pollsters as a trend and never converts point estimates into an
election forecast.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import ssl
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .cec_open_data import JURISDICTION_ALIASES
from .models import SourceFetchResult, utc_now_iso
from .source_registry import PollSource


TVBS_POLL_CENTER_URL = "https://www.tvbs.com.tw/poll-center"
POLL_TTL_DAYS = 30


class TVBSPollError(RuntimeError):
    """Raised when a TVBS primary poll report cannot be parsed safely."""


def _download_bytes(url: str) -> bytes:
    encoded = urllib.parse.quote(url, safe=":/?=&%")
    request = urllib.request.Request(
        encoded,
        headers={
            "User-Agent": "ogasawara-election-analysis/1.2",
            "Accept": "text/html,application/pdf,application/octet-stream,*/*",
        },
    )
    context = ssl.create_default_context()
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", None)
    if strict_flag is not None:
        context.verify_flags &= ~strict_flag
    with urllib.request.urlopen(request, timeout=90, context=context) as response:
        data = response.read()
    if not data:
        raise TVBSPollError(f"empty response from {url}")
    return data


def _compact(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _match_text(value: str) -> str:
    return _compact(value).replace("臺", "台").replace("县", "縣").replace("兰", "蘭").replace("义", "義")


def _iso_date(year: int, month: int, day: int) -> str:
    if year < 1911:
        year += 1911
    return dt.date(year, month, day).isoformat()


def _roc_date_from_text(text: str) -> str:
    match = re.search(r"(\d{2,3})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if not match:
        return ""
    y, m, d = (int(part) for part in match.groups())
    return _iso_date(y, m, d)


class _PollIndexParser(HTMLParser):
    """Collect PDF anchors plus the date in the surrounding table row."""

    def __init__(self):
        super().__init__()
        self.entries: List[Dict[str, str]] = []
        self._in_row = False
        self._row_text: List[str] = []
        self._row_anchors: List[Tuple[str, str]] = []
        self._href: Optional[str] = None
        self._anchor_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._in_row = True
            self._row_text = []
            self._row_anchors = []
        if tag == "a":
            attrs_dict = {key: value for key, value in attrs}
            self._href = attrs_dict.get("href")
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._in_row:
            self._row_text.append(data)
        if self._href is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._href is not None:
            title = _compact(" ".join(self._anchor_text))
            if self._in_row:
                self._row_anchors.append((self._href, title))
            else:
                self._append_entry(self._href, title, "")
            self._href = None
            self._anchor_text = []
        elif tag == "tr" and self._in_row:
            row_text = _compact(" ".join(self._row_text))
            listing_date = _roc_date_from_text(row_text)
            for href, title in self._row_anchors:
                self._append_entry(href, title, listing_date)
            self._in_row = False
            self._row_text = []
            self._row_anchors = []

    def _append_entry(self, href: str, title: str, listing_date: str) -> None:
        if not href or ".pdf" not in href.lower() or not title:
            return
        self.entries.append(
            {"href": href, "title": title, "listing_date": listing_date}
        )


class TVBSPollCenterAdapter(PollSource):
    """Public poll adapter backed by TVBS's own reports."""

    source_id = "tvbs_poll_center"
    source_grade = "C"
    priority = 20

    def __init__(
        self,
        index_url: str = TVBS_POLL_CENTER_URL,
        fetcher: Any = None,
        pdf_text_parser: Any = None,
    ):
        self.index_url = index_url
        self.fetcher = fetcher or _download_bytes
        self.pdf_text_parser = pdf_text_parser or self._extract_pdf_text

    def supports(self, jurisdiction: str, election_type: str, target_year: int) -> bool:
        return (
            election_type == "county_mayor"
            and int(target_year) == 2026
            and bool(str(jurisdiction).strip())
        )

    def metadata(self) -> Dict[str, Any]:
        base = super().metadata()
        base.update(
            {
                "index_url": self.index_url,
                "supported_elections": {"county_mayor": [2026]},
                "method_policy": "primary_pdf_required",
                "source_role": "poll_calibration_only",
            }
        )
        return base

    def fetch(self, jurisdiction: str, election_type: str, target_year: int) -> SourceFetchResult:
        if not self.supports(jurisdiction, election_type, target_year):
            return SourceFetchResult(
                source_id=self.source_id,
                source_grade=self.source_grade,
                records=[],
                warnings=[f"unsupported TVBS poll query: {jurisdiction} {election_type} {target_year}"],
            )

        page_bytes = self.fetcher(self.index_url)
        html = page_bytes.decode("utf-8", errors="replace")
        entries = self._parse_index(html)
        official_jurisdiction = JURISDICTION_ALIASES.get(jurisdiction, jurisdiction)
        matches = [
            entry
            for entry in entries
            if self._entry_matches(entry, official_jurisdiction)
        ]

        if not matches:
            return SourceFetchResult(
                source_id=self.source_id,
                source_grade=self.source_grade,
                records=[],
                warnings=[f"TVBS poll center listed no 2026 county/city mayor poll for {jurisdiction}"],
                raw_reference=self.index_url,
            )

        records: List[Dict[str, Any]] = []
        warnings: List[str] = []
        used: List[str] = []
        hashes: List[str] = []

        for entry in matches:
            pdf_url = urllib.parse.urljoin(self.index_url, entry["href"])
            try:
                pdf_bytes = self.fetcher(pdf_url)
                pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
                text = self.pdf_text_parser(pdf_bytes)
                record = self._parse_report(
                    text=text,
                    jurisdiction=jurisdiction,
                    official_jurisdiction=official_jurisdiction,
                    title=entry["title"],
                    listing_date=entry.get("listing_date", ""),
                    pdf_url=pdf_url,
                    pdf_sha=pdf_sha,
                )
            except Exception as exc:
                warnings.append(f"TVBS primary PDF parse failed for {pdf_url}: {exc}")
                continue

            if record is None:
                warnings.append(f"TVBS primary PDF lacked required poll metadata: {pdf_url}")
                continue
            records.append(record)
            used.append(pdf_url)
            hashes.append(pdf_sha)

        if not records:
            warnings.append(f"no valid TVBS poll records could be built for {jurisdiction}")

        source_version = ""
        if hashes:
            source_version = "pdf_sha256_set:" + hashlib.sha256(
                "|".join(sorted(hashes)).encode("utf-8")
            ).hexdigest()

        return SourceFetchResult(
            source_id=self.source_id,
            source_grade=self.source_grade,
            records=records,
            warnings=warnings,
            source_version=source_version,
            raw_reference=";".join(used) or self.index_url,
        )

    def _parse_index(self, html: str) -> List[Dict[str, str]]:
        parser = _PollIndexParser()
        parser.feed(html)
        return parser.entries

    @staticmethod
    def _entry_matches(entry: Dict[str, str], official_jurisdiction: str) -> bool:
        title = _match_text(entry.get("title", ""))
        jurisdiction = _match_text(official_jurisdiction)
        return (
            "2026" in title
            and jurisdiction in title
            and ("縣長" in title or "市長" in title)
            and "民調" in title
        )

    @staticmethod
    def _extract_pdf_text(pdf_bytes: bytes) -> str:
        try:
            import pdfplumber
        except ImportError as exc:
            raise TVBSPollError("pdfplumber is required for TVBS poll PDFs") from exc

        pages: List[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                if text:
                    pages.append(text)
        result = _compact("\n".join(pages))
        if not result:
            raise TVBSPollError("TVBS poll PDF contained no extractable text")
        return result

    def _parse_report(
        self,
        text: str,
        jurisdiction: str,
        official_jurisdiction: str,
        title: str,
        listing_date: str,
        pdf_url: str,
        pdf_sha: str,
    ) -> Optional[Dict[str, Any]]:
        body = _compact(text)
        field_dates = self._field_dates(body)
        sample_size = self._sample_size(body)
        moe = self._moe(body)
        method = self._method(body)
        support, undecided, scenario, question_context = self._support_scenario(body)

        weighting = self._weighting(body)
        sampling = self._sampling(body)
        sample_frame = self._sample_frame(body, official_jurisdiction)

        required = {
            "field_dates": field_dates,
            "sample_size": sample_size,
            "moe": moe,
            "method": method,
            "sample_frame": sample_frame,
            "sampling": sampling,
            "weighting": weighting,
            "support": support,
            "undecided": undecided,
            "question_context": question_context,
        }
        if any(value in (None, "", [], ()) for value in required.values()):
            return None

        field_start, field_end = field_dates
        publish_date = self._publish_date(pdf_url, listing_date) or field_end
        try:
            expires_at = (
                dt.date.fromisoformat(publish_date) + dt.timedelta(days=POLL_TTL_DAYS)
            ).isoformat()
        except ValueError:
            expires_at = ""

        now = utc_now_iso()

        poll_id_seed = f"{pdf_url}|{scenario}|{official_jurisdiction}"
        poll_id = "tvbs-" + hashlib.sha1(poll_id_seed.encode("utf-8")).hexdigest()[:20]

        return {
            "poll_id": poll_id,
            "pollster": "TVBS民意調查中心",
            "commissioner": "self",
            "jurisdiction": jurisdiction,
            "official_jurisdiction": official_jurisdiction,
            "election_type": "county_mayor",
            "election_year": 2026,
            "scenario": scenario,
            "method": method,
            "sample_size": int(sample_size),
            "sample_frame": sample_frame,
            "sampling": sampling,
            "weighting": weighting,
            "field_start": field_start,
            "field_end": field_end,
            "publish_date": publish_date,
            "listing_date": listing_date,
            "moe": float(moe),
            "moe_applicable": True,
            "confidence_level": 0.95,
            "undecided": round(float(undecided), 6),
            "question_wording": question_context,
            "question_wording_is_verbatim": False,
            "candidate_support": support,
            "cross_tabs_available": ("交叉分析" in body or "交叉表" in body),
            "campaign_claim": False,
            "funding_source": "TVBS" if "調查經費來源為TVBS" in body.replace(" ", "") else "",
            "source": self.index_url,
            "source_reference": pdf_url,
            "source_id": self.source_id,
            "source_grade": self.source_grade,
            "source_version": f"pdf_sha256:{pdf_sha}",
            "report_title": title,
            "retrieved_at": now,
            "last_verified_at": now,
            "expires_at": expires_at,
            "record_type": "poll",
            "notes": (
                "Primary TVBS poll-center PDF. Point estimates are calibration data only; "
                "no cross-pollster trend or election forecast is implied."
            ),
        }

    @staticmethod
    def _field_dates(text: str) -> Optional[Tuple[str, str]]:
        patterns = [
            r"(\d{2,4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*(?:至|到|－|-|~|～)\s*(?:(\d{2,4})年\s*)?(?:(\d{1,2})月\s*)?(\d{1,2})日?",
            r"(\d{2,4})[./-](\d{1,2})[./-](\d{1,2})\s*(?:至|到|－|-|~|～)\s*(?:(\d{2,4})[./-])?(?:(\d{1,2})[./-])?(\d{1,2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            y1, m1, d1, y2, m2, d2 = match.groups()
            year1 = int(y1)
            month1 = int(m1)
            day1 = int(d1)
            year2 = int(y2) if y2 else year1
            month2 = int(m2) if m2 else month1
            try:
                return (
                    _iso_date(year1, month1, day1),
                    _iso_date(year2, month2, int(d2)),
                )
            except ValueError:
                continue
        return None

    @staticmethod
    def _sample_size(text: str) -> Optional[int]:
        patterns = [
            r"(?:最後)?成功訪問(?:有效樣本)?\s*([0-9,]+)\s*位",
            r"有效樣本\s*([0-9,]+)\s*位",
            r"完成\s*([0-9,]+)\s*份有效樣本",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1).replace(",", ""))
        return None

    @staticmethod
    def _moe(text: str) -> Optional[float]:
        match = re.search(
            r"抽樣誤差(?:為|約為|在)?\s*[±＋+\-－]\s*(\d+(?:\.\d+)?)\s*(?:個)?百分點",
            text,
        )
        return float(match.group(1)) if match else None

    @staticmethod
    def _method(text: str) -> str:
        normalized = text.replace(" ", "")
        has_mobile = "手機" in normalized or "行動電話" in normalized
        has_landline = "市內電話" in normalized or "住宅電話" in normalized or "電話號碼後" in normalized
        if has_mobile and has_landline:
            return "telephone_mixed"
        if has_mobile:
            return "telephone_mobile"
        if has_landline:
            return "telephone_landline"
        return ""

    @staticmethod
    def _sampling(text: str) -> str:
        patterns = [
            r"(抽樣方法採用[^。]{1,120})",
            r"(採用[^。]{0,80}(?:後四碼|後兩碼|RDD)[^。]{0,80}(?:抽樣|隨機抽樣))",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return _compact(match.group(1))
        return ""

    @staticmethod
    def _weighting(text: str) -> str:
        patterns = [
            r"(所有資料並依[^。]{1,160}加權[^。]{0,40})",
            r"(資料[^。]{0,60}(?:性別|年齡)[^。]{0,140}加權[^。]{0,40})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return _compact(match.group(1))
        return ""

    @staticmethod
    def _sample_frame(text: str, official_jurisdiction: str) -> str:
        escaped = re.escape(official_jurisdiction)
        patterns = [
            rf"(\d{{2}}歲以上{escaped}(?:縣民|市民|民眾)?)",
            rf"(戶籍[^。]{{0,50}}{escaped}[^。]{{0,50}}\d{{2}}歲以上[^。]{{0,30}})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return _compact(match.group(1))
        return ""

    @staticmethod
    def _publish_date(pdf_url: str, listing_date: str) -> str:
        match = re.search(r"/(20\d{6})/", pdf_url)
        if match:
            token = match.group(1)
            try:
                return dt.datetime.strptime(token, "%Y%m%d").date().isoformat()
            except ValueError:
                pass
        return listing_date

    @staticmethod
    def _support_scenario(
        text: str,
    ) -> Tuple[List[Dict[str, Any]], Optional[float], str, str]:
        normalized = _compact(text)
        undecided_match = re.search(
            r"(\d+(?:\.\d+)?)\s*%\s*(?:尚未決定支持對象|尚未決定|未決定支持對象|無法決定)",
            normalized,
        )
        if not undecided_match:
            return [], None, "", ""

        start = max(0, undecided_match.start() - 900)
        end = min(len(normalized), undecided_match.end() + 80)
        segment = normalized[start:end]

        party_names = {
            "民進黨": "民主進步黨",
            "國民黨": "中國國民黨",
            "民眾黨": "台灣民眾黨",
            "無黨籍": "無黨籍",
        }
        patterns = [
            r"(民進黨|國民黨|民眾黨|無黨籍)?\s*([\u4e00-\u9fff．·]{2,8})支持度(?:則)?(?:為|是|達)?\s*(\d+(?:\.\d+)?)\s*%",
            r"(民進黨|國民黨|民眾黨|無黨籍)?\s*([\u4e00-\u9fff．·]{2,8})(?:則)?獲得\s*(\d+(?:\.\d+)?)\s*%\s*(?:表態)?支持",
            r"(民進黨|國民黨|民眾黨|無黨籍)\s*([\u4e00-\u9fff．·]{2,8})\s*[（(]\s*(\d+(?:\.\d+)?)\s*%\s*[）)]",
        ]

        raw_hits: List[Tuple[int, str, str, float]] = []
        for pattern in patterns:
            for match in re.finditer(pattern, segment):
                party = match.group(1) or ""
                name = _compact(match.group(2))
                value = float(match.group(3))
                if not (0 <= value <= 100):
                    continue
                if any(token in name for token in ("選民", "民眾", "支持度", "候選人", "縣長", "市長")):
                    continue
                raw_hits.append((match.start(), party, name, value))

        raw_hits.sort(key=lambda item: item[0])
        seen: Dict[str, Dict[str, Any]] = {}
        for _pos, party, name, value in raw_hits:
            if name not in seen:
                seen[name] = {
                    "candidate": name,
                    "support": round(value / 100.0, 6),
                    "party": party_names.get(party, party),
                }

        support = list(seen.values())
        # A mayoral scenario must contain at least two named candidates.
        if len(support) < 2:
            return [], None, "", ""

        scenario = "candidate_support_scenario"
        if re.search(r"三人參選|三腳督|藍綠白三方", segment):
            scenario = "three_candidate"
        elif re.search(r"兩人對決|藍綠對決|二人對決", segment):
            scenario = "two_candidate"

        # Store a short source-context excerpt, explicitly not as a verbatim
        # questionnaire unless the PDF itself exposes one in future adapters.
        context_start = max(0, segment.find(support[0]["candidate"]) - 80)
        question_context = _compact(segment[context_start:undecided_match.end() - start])
        if len(question_context) > 320:
            question_context = question_context[-320:]
        question_context = "報告情境摘錄（非問卷逐字稿）：" + question_context

        return support, float(undecided_match.group(1)) / 100.0, scenario, question_context
