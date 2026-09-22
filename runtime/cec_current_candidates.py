"""Official CEC current-candidate registration adapter for the 2026 local election."""

from __future__ import annotations

import hashlib
import io
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .cec_open_data import JURISDICTION_ALIASES
from .models import SourceFetchResult, utc_now_iso
from .source_registry import CurrentCandidateSource


CEC_2026_REGISTRATION_PAGE = "https://web.cec.gov.tw/central/article/64733"
CEC_2026_ELECTION_DATE = "2026-11-28"
CEC_2026_PUBLISHED_DATE = "2026-09-07"


class CECCandidateError(RuntimeError):
    pass


class _AnchorCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors: List[Tuple[str, str]] = []
        self._href: Optional[str] = None
        self._text: List[str] = []

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {key: value for key, value in attrs}
        self._href = attrs_dict.get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = " ".join(part.strip() for part in self._text if part.strip())
        self.anchors.append((self._href, text))
        self._href = None
        self._text = []


def _download_bytes(url: str) -> bytes:
    encoded = urllib.parse.quote(url, safe=":/?=&%")
    request = urllib.request.Request(
        encoded,
        headers={
            "User-Agent": "ogasawara-election-analysis/1.3",
            "Accept": "text/html,application/pdf,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    if not data:
        raise CECCandidateError(f"empty response from {url}")
    return data


def _clean_cell(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"[\r\n\t]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _roc_date_to_iso(value: str) -> str:
    match = re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", _clean_cell(value))
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    return f"{year + 1911:04d}-{month:02d}-{day:02d}"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CECCurrentCandidateAdapter(CurrentCandidateSource):
    source_id = "cec_current_candidates"
    source_grade = "A"
    priority = 1

    def __init__(
        self,
        page_url: str = CEC_2026_REGISTRATION_PAGE,
        fetcher: Any = None,
        pdf_table_parser: Any = None,
    ):
        self.page_url = page_url
        self.fetcher = fetcher or _download_bytes
        self.pdf_table_parser = pdf_table_parser or self._extract_pdf_tables

    def supports(self, jurisdiction: str, election_type: str, target_year: int) -> bool:
        return election_type == "county_mayor" and int(target_year) == 2026 and bool(jurisdiction)

    def metadata(self) -> Dict[str, Any]:
        base = super().metadata()
        base.update(
            {
                "page_url": self.page_url,
                "supported_elections": {"county_mayor": [2026]},
                "candidate_status": "registered",
            }
        )
        return base

    def fetch(self, jurisdiction: str, election_type: str, target_year: int) -> SourceFetchResult:
        if not self.supports(jurisdiction, election_type, target_year):
            return SourceFetchResult(
                source_id=self.source_id,
                source_grade=self.source_grade,
                records=[],
                warnings=[f"unsupported current-candidate query: {jurisdiction} {election_type} {target_year}"],
            )

        page_bytes = self.fetcher(self.page_url)
        try:
            html = page_bytes.decode("utf-8")
        except UnicodeDecodeError:
            html = page_bytes.decode("utf-8", errors="replace")
        links = self._candidate_pdf_links(html)
        if not links:
            return SourceFetchResult(
                source_id=self.source_id,
                source_grade=self.source_grade,
                records=[],
                warnings=["CEC registration page contained no mayor registration summary PDFs"],
            )

        official_jurisdiction = JURISDICTION_ALIASES.get(jurisdiction, jurisdiction)
        all_records: List[Dict[str, Any]] = []
        used_links: List[str] = []
        hashes: List[str] = []
        warnings: List[str] = []
        now = utc_now_iso()

        for link in links:
            try:
                pdf_bytes = self.fetcher(link)
                hashes.append(_sha256_bytes(pdf_bytes))
                tables = self.pdf_table_parser(pdf_bytes)
            except Exception as exc:
                warnings.append(f"candidate PDF parse failed for {link}: {exc}")
                continue

            rows = self._flatten_tables(tables)
            records = self._rows_to_records(
                rows,
                requested_jurisdiction=jurisdiction,
                official_jurisdiction=official_jurisdiction,
                source_url=link,
                verified_at=now,
            )
            if records:
                all_records.extend(records)
                used_links.append(link)

        # Candidate names should be unique within a county/city mayor race.
        deduped: Dict[str, Dict[str, Any]] = {}
        for record in all_records:
            deduped[record["candidate_id"]] = record
        records = list(deduped.values())

        if not records:
            warnings.append(f"no registered 2026 county/city mayor candidates found for {jurisdiction}")

        version_hash = hashlib.sha256(
            "|".join(sorted(hashes)).encode("utf-8")
        ).hexdigest() if hashes else ""
        return SourceFetchResult(
            source_id=self.source_id,
            source_grade=self.source_grade,
            records=records,
            warnings=warnings,
            source_version=f"pdf_sha256:{version_hash}" if version_hash else "",
            raw_reference=";".join(used_links) or self.page_url,
        )

    def _candidate_pdf_links(self, html: str) -> List[str]:
        parser = _AnchorCollector()
        parser.feed(html)
        links: List[str] = []
        for href, text in parser.anchors:
            label = _clean_cell(text)
            if not href or ".pdf" not in href.lower():
                continue
            if "市長選舉候選人登記彙總表" not in label:
                continue
            if "政黨推薦候選人登記情形" in label:
                continue
            if not (label.startswith("1-1") or label.startswith("3-1")):
                continue
            links.append(urllib.parse.urljoin(self.page_url, href))
        return sorted(set(links))

    @staticmethod
    def _extract_pdf_tables(pdf_bytes: bytes) -> List[List[List[Any]]]:
        try:
            import pdfplumber
        except ImportError as exc:
            raise CECCandidateError(
                "pdfplumber is required for official candidate-registration PDFs"
            ) from exc

        tables: List[List[List[Any]]] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_tables = page.extract_tables() or []
                tables.extend(page_tables)
        if not tables:
            raise CECCandidateError("no extractable tables found in official CEC candidate PDF")
        return tables

    @staticmethod
    def _flatten_tables(tables: List[List[List[Any]]]) -> List[List[str]]:
        rows: List[List[str]] = []
        for table in tables:
            for row in table or []:
                if not row:
                    continue
                values = [_clean_cell(value) for value in row]
                if any(values):
                    rows.append(values)
        return rows

    def _rows_to_records(
        self,
        rows: List[List[str]],
        requested_jurisdiction: str,
        official_jurisdiction: str,
        source_url: str,
        verified_at: str,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for row in rows:
            if len(row) < 4:
                continue
            region = _clean_cell(row[0])
            registration_date = _roc_date_to_iso(row[1])
            name = _clean_cell(row[2])
            recommendation = _clean_cell(row[3])
            notes = _clean_cell(row[4]) if len(row) > 4 else ""

            if region in {"選舉區", "选举区"}:
                continue
            if region != official_jurisdiction:
                continue
            if not registration_date or not name:
                continue

            recommended_by = recommendation if recommendation and recommendation != "無" else ""
            digest = hashlib.sha1(
                f"2026|county_mayor|{official_jurisdiction}|{name}|{registration_date}".encode("utf-8")
            ).hexdigest()[:16]
            records.append(
                {
                    "candidate_id": f"cec-reg-2026-{digest}",
                    "name": name,
                    "candidate_name": name,
                    "election_type": "county_mayor",
                    "election_year": 2026,
                    "jurisdiction": requested_jurisdiction,
                    "official_jurisdiction": official_jurisdiction,
                    "candidate_status": "registered",
                    "registration_date": registration_date,
                    "recommended_by_party": recommended_by,
                    "party": recommended_by or "未由政黨推薦",
                    "source": self.page_url,
                    "source_id": self.source_id,
                    "source_grade": "A",
                    "evidence_grade": "A",
                    "source_reference": source_url,
                    "retrieved_at": verified_at,
                    "last_verified_at": verified_at,
                    "valid_until": CEC_2026_ELECTION_DATE,
                    "published_at": CEC_2026_PUBLISHED_DATE,
                    "time_scope": "2026 local election registration",
                    "sources": [
                        {
                            "source": "中央選舉委員會",
                            "source_grade": "A",
                            "reference": source_url,
                            "accessed_at": verified_at[:10],
                        }
                    ],
                    "notes": notes,
                }
            )
        return records
