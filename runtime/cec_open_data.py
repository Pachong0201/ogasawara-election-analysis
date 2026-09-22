"""Official Central Election Commission (CEC) votedata.zip adapter.

This adapter reads the CEC "選舉資料庫(含選舉區資料)" archive published at
https://data.cec.gov.tw/選舉資料庫/votedata.zip and converts stable historical
results into the runtime election-record schema.

Supported V1.2 slices:
- president: 2016, 2020, 2024
- regional_legislator: 2016, 2020, 2024
- county_mayor: 2014, 2018, 2022
- geography level: township_district (primary) and county_city (aggregate)

The archive is cached locally. It is never committed to the repository.
"""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
import ssl
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import DataQuery, SourceFetchResult, utc_now_iso
from .source_registry import ElectionDataSource


CEC_DATASET_PAGE = "https://data.gov.tw/dataset/13119"
CEC_VOTEDATA_URL = "https://data.cec.gov.tw/選舉資料庫/votedata.zip"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 600

ELECTION_DATES = {
    ("county_mayor", 2014): "2014-11-29",
    ("president", 2016): "2016-01-16",
    ("regional_legislator", 2016): "2016-01-16",
    ("county_mayor", 2018): "2018-11-24",
    ("president", 2020): "2020-01-11",
    ("regional_legislator", 2020): "2020-01-11",
    ("county_mayor", 2022): "2022-11-26",
    ("president", 2024): "2024-01-13",
    ("regional_legislator", 2024): "2024-01-13",
}

# Exact path families confirmed from the official archive structure. 2016 uses
# suffixed filenames, so member discovery intentionally searches by stem.
PATH_FAMILIES: Dict[Tuple[str, int], Sequence[str]] = {
    ("president", 2016): ("2016總統立委/總統",),
    ("president", 2020): ("2020總統立委/總統",),
    ("president", 2024): ("2024總統立委/總統",),
    ("regional_legislator", 2016): ("2016總統立委/區域立委",),
    ("regional_legislator", 2020): ("2020總統立委/區域立委",),
    ("regional_legislator", 2024): ("2024總統立委/區域立委",),
    ("county_mayor", 2014): (
        "2014-103年地方公職人員選舉/直轄市市長",
        "2014-103年地方公職人員選舉/縣市市長",
    ),
    ("county_mayor", 2018): (
        "2018-107年地方公職人員選舉/直轄市市長",
        "2018-107年地方公職人員選舉/縣市市長",
    ),
    ("county_mayor", 2022): (
        "2022-111年地方公職人員選舉/C1/prv",
        "2022-111年地方公職人員選舉/C1/city",
    ),
}

ZERO_TOKENS = {"", "0", "00", "000", "0000", "00000"}

JURISDICTION_ALIASES = {
    "台北市": "臺北市",
    "臺北市": "臺北市",
    "新北市": "新北市",
    "桃园市": "桃園市",
    "桃園市": "桃園市",
    "台中市": "臺中市",
    "臺中市": "臺中市",
    "台南市": "臺南市",
    "臺南市": "臺南市",
    "高雄市": "高雄市",
    "宜兰县": "宜蘭縣",
    "宜蘭縣": "宜蘭縣",
    "新竹县": "新竹縣",
    "新竹縣": "新竹縣",
    "苗栗县": "苗栗縣",
    "苗栗縣": "苗栗縣",
    "彰化县": "彰化縣",
    "彰化縣": "彰化縣",
    "南投县": "南投縣",
    "南投縣": "南投縣",
    "云林县": "雲林縣",
    "雲林縣": "雲林縣",
    "嘉义县": "嘉義縣",
    "嘉義縣": "嘉義縣",
    "屏东县": "屏東縣",
    "屏東縣": "屏東縣",
    "台东县": "臺東縣",
    "臺東縣": "臺東縣",
    "花莲县": "花蓮縣",
    "花蓮縣": "花蓮縣",
    "澎湖县": "澎湖縣",
    "澎湖縣": "澎湖縣",
    "基隆市": "基隆市",
    "新竹市": "新竹市",
    "嘉义市": "嘉義市",
    "嘉義市": "嘉義市",
    "金门县": "金門縣",
    "金門縣": "金門縣",
    "连江县": "連江縣",
    "連江縣": "連江縣",
}


class CECOpenDataError(RuntimeError):
    """Raised when the official archive cannot be downloaded or parsed safely."""


@dataclass
class ArchiveInfo:
    path: Path
    sha256: str
    size: int


def _clean(value: Any) -> str:
    return str(value or "").strip().strip("'\"").strip()


def _is_zero(value: Any) -> bool:
    return _clean(value) in ZERO_TOKENS


def _decode_csv(content: bytes) -> List[List[str]]:
    text: Optional[str] = None
    for encoding in ("utf-8-sig", "cp950", "big5"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise CECOpenDataError("CEC CSV could not be decoded as UTF-8/CP950/Big5")

    rows: List[List[str]] = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not any(_clean(value) for value in row):
            continue
        rows.append([_clean(value) for value in row])
    return rows


def _quoted_url(url: str) -> str:
    return urllib.parse.quote(url, safe=":/?=&%")


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", None)
    if strict_flag is not None:
        context.verify_flags &= ~strict_flag
    return context


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CECArchive:
    """Download/cache/open the official CEC election ZIP."""

    def __init__(
        self,
        cache_dir: Path,
        source_url: str = CEC_VOTEDATA_URL,
        archive_path: Optional[Path] = None,
        opener: Any = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.source_url = source_url
        self.archive_path = Path(archive_path) if archive_path else self.cache_dir / "votedata.zip"
        self.opener = opener

    def ensure(self, force: bool = False) -> ArchiveInfo:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.archive_path.exists() and self.archive_path.stat().st_size > 0 and not force:
            return ArchiveInfo(self.archive_path, _sha256(self.archive_path), self.archive_path.stat().st_size)

        if self.opener is not None:
            response = self.opener(_quoted_url(self.source_url))
        else:
            request = urllib.request.Request(
                _quoted_url(self.source_url),
                headers={
                    "User-Agent": "ogasawara-election-analysis/1.2",
                    "Accept": "application/zip,application/octet-stream,*/*",
                },
            )
            try:
                response = urllib.request.urlopen(
                    request,
                    timeout=DOWNLOAD_TIMEOUT_SECONDS,
                    context=_ssl_context(),
                )
            except Exception as exc:
                raise CECOpenDataError(f"CEC archive download failed: {exc}") from exc

        tmp = self.archive_path.with_suffix(".zip.tmp")
        written = 0
        try:
            with response:
                with tmp.open("wb") as out:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_ARCHIVE_BYTES:
                            raise CECOpenDataError("CEC archive exceeded configured size limit")
                        out.write(chunk)
            if written == 0:
                raise CECOpenDataError("CEC archive download returned zero bytes")
            try:
                with zipfile.ZipFile(tmp, "r", metadata_encoding="cp950") as zf:
                    if zf.testzip() is not None:
                        raise CECOpenDataError("CEC archive failed ZIP CRC validation")
            except TypeError:
                with zipfile.ZipFile(tmp, "r") as zf:
                    if zf.testzip() is not None:
                        raise CECOpenDataError("CEC archive failed ZIP CRC validation")
            except zipfile.BadZipFile as exc:
                raise CECOpenDataError("CEC source is not a valid ZIP archive") from exc
            tmp.replace(self.archive_path)
        finally:
            if tmp.exists():
                tmp.unlink()

        return ArchiveInfo(self.archive_path, _sha256(self.archive_path), self.archive_path.stat().st_size)

    @staticmethod
    def open(path: Path) -> zipfile.ZipFile:
        try:
            return zipfile.ZipFile(path, "r", metadata_encoding="cp950")
        except TypeError:
            return zipfile.ZipFile(path, "r")


class CECOpenDataAdapter(ElectionDataSource):
    """Real official election-results adapter backed by CEC votedata.zip."""

    source_id = "cec_open_data"
    source_grade = "A"
    priority = 1

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        archive_path: Optional[Path] = None,
        source_url: str = CEC_VOTEDATA_URL,
        opener: Any = None,
    ):
        repo_root = Path(__file__).resolve().parents[1]
        self.archive = CECArchive(
            cache_dir=Path(cache_dir) if cache_dir else repo_root / "cache" / "raw" / "cec",
            source_url=source_url,
            archive_path=archive_path,
            opener=opener,
        )

    def metadata(self) -> Dict[str, Any]:
        base = super().metadata()
        base.update(
            {
                "dataset_page": CEC_DATASET_PAGE,
                "download_url": self.archive.source_url,
                "supported": {
                    "president": [2016, 2020, 2024],
                    "regional_legislator": [2016, 2020, 2024],
                    "county_mayor": [2014, 2018, 2022],
                },
                "levels": ["township_district", "county_city"],
            }
        )
        return base

    def supports(self, query: DataQuery) -> bool:
        return (
            (query.election_type, int(query.year)) in PATH_FAMILIES
            and query.level in {"township_district", "county_city"}
            and bool(str(query.jurisdiction).strip())
        )

    def fetch(self, query: DataQuery) -> SourceFetchResult:
        if not self.supports(query):
            return SourceFetchResult(
                source_id=self.source_id,
                source_grade=self.source_grade,
                records=[],
                warnings=[f"unsupported CEC query: {query.to_dict()}"],
            )

        archive_info = self.archive.ensure()
        records: List[Dict[str, Any]] = []
        warnings: List[str] = []
        used_members: List[str] = []

        with self.archive.open(archive_info.path) as zf:
            names = [info.filename for info in zf.infolist()]
            for family in PATH_FAMILIES[(query.election_type, int(query.year))]:
                file_map = self._resolve_family_members(names, family)
                if not file_map:
                    continue
                try:
                    part_records = self._parse_family(zf, query, file_map, archive_info.sha256)
                except CECOpenDataError as exc:
                    warnings.append(f"{family}: {exc}")
                    continue
                if part_records:
                    records.extend(part_records)
                    used_members.extend(file_map.values())

        # Duplicate rows can occur only if two path families both contain the same
        # target jurisdiction; dedupe on region/candidate, but fail later in normalizer
        # if a genuine duplicate record still survives.
        deduped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for record in records:
            key = (
                str(record.get("jurisdiction") or ""),
                str(record.get("candidate_id") or record.get("candidate_name") or ""),
                str(record.get("election_year") or ""),
            )
            deduped[key] = record
        records = list(deduped.values())

        if not records:
            warnings.append(
                f"CEC archive contained no {query.level} records for "
                f"{query.jurisdiction} {query.year} {query.election_type}"
            )

        return SourceFetchResult(
            source_id=self.source_id,
            source_grade=self.source_grade,
            records=records,
            warnings=warnings,
            source_version=f"sha256:{archive_info.sha256}",
            raw_reference=";".join(sorted(set(used_members))) or self.archive.source_url,
        )

    @staticmethod
    def _member_name_candidates(stem: str) -> Tuple[str, ...]:
        return (
            f"{stem}.csv",
            f"{stem}_P1.csv",
            f"{stem}_T1.csv",
        )

    def _resolve_family_members(self, names: Sequence[str], family: str) -> Dict[str, str]:
        matched = [name for name in names if f"/{family}/" in f"/{name}"]
        if not matched:
            return {}

        result: Dict[str, str] = {}
        for stem in ("elbase", "elcand", "elpaty", "elprof", "elctks"):
            options = [
                name for name in matched
                if any(name.endswith("/" + candidate) for candidate in self._member_name_candidates(stem))
            ]
            if not options:
                # 2016 may use suffixed files beyond the two known primary suffixes;
                # constrain by stem but keep discovery inside the exact election folder.
                options = [
                    name for name in matched
                    if name.rsplit("/", 1)[-1].startswith(stem + "_")
                    and name.lower().endswith(".csv")
                ]
            if not options:
                return {}
            # Prefer shortest path/name; exact stem beats a suffixed fallback.
            result[stem] = sorted(options, key=lambda value: (len(value.rsplit("/", 1)[-1]), len(value)))[0]
        return result

    @staticmethod
    def _read(zf: zipfile.ZipFile, member: str) -> List[List[str]]:
        try:
            return _decode_csv(zf.read(member))
        except KeyError as exc:
            raise CECOpenDataError(f"archive member missing: {member}") from exc

    def _parse_family(
        self,
        zf: zipfile.ZipFile,
        query: DataQuery,
        members: Dict[str, str],
        archive_sha: str,
    ) -> List[Dict[str, Any]]:
        base = self._read(zf, members["elbase"])
        candidates = self._read(zf, members["elcand"])
        parties = self._read(zf, members["elpaty"])
        profile = self._read(zf, members["elprof"])
        votes = self._read(zf, members["elctks"])

        party_map = {
            _clean(row[0]): _clean(row[1])
            for row in parties if len(row) >= 2 and _clean(row[0])
        }

        official_jurisdiction = JURISDICTION_ALIASES.get(query.jurisdiction, query.jurisdiction)
        area_info = self._build_area_info(base, official_jurisdiction)
        county_codes: set[Tuple[str, str]] = area_info["county_codes"]
        if not county_codes:
            return []

        candidate_map = self._build_candidate_map(candidates, party_map, query)
        profile_map = self._build_profile_map(profile)

        level = query.level
        records: List[Dict[str, Any]] = []
        for row in votes:
            if len(row) < 10:
                continue
            prov, county, district, town, village, station = [_clean(value) for value in row[:6]]
            if (prov, county) not in county_codes:
                continue

            if level == "township_district":
                if _is_zero(town) or not _is_zero(village) or not _is_zero(station):
                    continue
                region_name = area_info["townships"].get((prov, county, town))
                if not region_name:
                    continue
            else:
                if not _is_zero(town) or not _is_zero(village) or not _is_zero(station):
                    continue
                region_name = query.jurisdiction

            candidate_no = _clean(row[6])
            vote_count = self._int(row[7])
            if vote_count is None:
                continue

            candidate = self._candidate_for(
                candidate_map,
                query,
                prov,
                county,
                district,
                candidate_no,
            )
            if not candidate:
                continue

            pkey = self._profile_key(prov, county, district, town, village, station)
            prof = profile_map.get(pkey)
            if prof is None and level == "county_city":
                # Some archives expose multiple county aggregates distinguished by
                # election district. Sum only when exact county aggregate is absent.
                prof = self._aggregate_county_profile(profile, prov, county)
            if prof is None:
                continue

            valid_votes = prof["valid_votes"]
            total_cast = prof["total_cast"]
            electors = prof["electors"]
            if valid_votes <= 0 or vote_count > valid_votes:
                continue
            turnout = (total_cast / electors) if electors else 0.0

            region_id = self._region_id(prov, county, town if level == "township_district" else "000")
            cand_id = self._candidate_id(query, candidate, prov, county, district, candidate_no)
            records.append(
                {
                    "record_id": (
                        f"cec|{query.election_type}|{query.year}|{region_id}|{cand_id}"
                    ),
                    "election_type": query.election_type,
                    "election_year": int(query.year),
                    "election_date": ELECTION_DATES[(query.election_type, int(query.year))],
                    "jurisdiction": region_name,
                    "parent_jurisdiction": query.jurisdiction,
                    "official_parent_jurisdiction": official_jurisdiction,
                    "level": level,
                    "candidate_id": cand_id,
                    "candidate_name": candidate["name"],
                    "party": candidate["party"],
                    "votes": vote_count,
                    "valid_votes": valid_votes,
                    "vote_share": round(vote_count / valid_votes, 8),
                    "turnout": round(turnout, 8),
                    "source": CEC_DATASET_PAGE,
                    "source_id": self.source_id,
                    "source_grade": self.source_grade,
                    "source_version": f"sha256:{archive_sha}",
                    "raw_reference": members["elctks"],
                    "source_reference": self.archive.source_url,
                    "retrieved_at": utc_now_iso(),
                    "verified_at": utc_now_iso(),
                    "boundary_version": "cec-township-2014-2024-v1",
                    "time_scope": str(query.year),
                    "normalization_version": "v1.2.0",
                    "region_id": region_id,
                    "cec_codes": {
                        "province_city": prov,
                        "county_city": county,
                        "election_district": district,
                        "township": town,
                        "village": village,
                        "polling_station": station,
                        "candidate_no": candidate_no,
                    },
                }
            )

        return records

    @staticmethod
    def _int(value: Any) -> Optional[int]:
        text = _clean(value).replace(",", "")
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def _profile_key(
        prov: str,
        county: str,
        district: str,
        town: str,
        village: str,
        station: str,
    ) -> Tuple[str, str, str, str, str, str]:
        def z(value: str, width: int) -> str:
            text = _clean(value)
            if _is_zero(text):
                return "0" * width
            return text
        return (
            z(prov, 2),
            z(county, 3),
            z(district, 2),
            z(town, 3),
            z(village, 4),
            z(station, 4),
        )

    def _build_profile_map(self, rows: Iterable[List[str]]) -> Dict[Tuple[str, ...], Dict[str, int]]:
        out: Dict[Tuple[str, ...], Dict[str, int]] = {}
        for row in rows:
            if len(row) < 10:
                continue
            key = self._profile_key(*[_clean(value) for value in row[:6]])
            valid = self._int(row[6])
            invalid = self._int(row[7])
            total_cast = self._int(row[8])
            electors = self._int(row[9])
            if None in (valid, invalid, total_cast, electors):
                continue
            if valid + invalid != total_cast:
                # Fail closed on semantic drift rather than trusting an apparently
                # plausible but re-ordered row.
                continue
            out[key] = {
                "valid_votes": int(valid),
                "invalid_votes": int(invalid),
                "total_cast": int(total_cast),
                "electors": int(electors),
            }
        return out

    def _aggregate_county_profile(
        self,
        rows: Iterable[List[str]],
        prov: str,
        county: str,
    ) -> Optional[Dict[str, int]]:
        candidates: List[Dict[str, int]] = []
        for row in rows:
            if len(row) < 10:
                continue
            p, c, _d, town, village, station = [_clean(value) for value in row[:6]]
            if p != prov or c != county or not _is_zero(town) or not _is_zero(village) or not _is_zero(station):
                continue
            valid = self._int(row[6])
            invalid = self._int(row[7])
            total_cast = self._int(row[8])
            electors = self._int(row[9])
            if None in (valid, invalid, total_cast, electors):
                continue
            if valid + invalid != total_cast:
                continue
            candidates.append(
                {
                    "valid_votes": int(valid),
                    "invalid_votes": int(invalid),
                    "total_cast": int(total_cast),
                    "electors": int(electors),
                }
            )
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Distinct electoral-district aggregate rows overlap for legislative
        # elections, so a synthetic county sum would double count. Refuse it.
        return None

    @staticmethod
    def _strip_parent(name: str, parent: str) -> str:
        text = _clean(name)
        if text.startswith(parent):
            text = text[len(parent):].strip()
        if " " in text:
            tail = text.split()[-1]
            if tail:
                text = tail
        return text

    def _build_area_info(self, rows: Iterable[List[str]], target: str) -> Dict[str, Any]:
        county_codes: set[Tuple[str, str]] = set()
        township_names: Dict[Tuple[str, str, str], str] = {}

        materialized = [row for row in rows if len(row) >= 6]
        for row in materialized:
            prov, county, _district, town, village, name = [_clean(value) for value in row[:6]]
            if _is_zero(town) and _is_zero(village) and self._strip_parent(name, "") == target:
                county_codes.add((prov, county))

        # Some files include election-district labels instead of a clean county
        # aggregate name. Fall back to an exact target prefix only if unique.
        if not county_codes:
            possible = set()
            for row in materialized:
                prov, county, _district, town, village, name = [_clean(value) for value in row[:6]]
                if _is_zero(town) and _is_zero(village) and name.startswith(target):
                    possible.add((prov, county))
            if len(possible) == 1:
                county_codes = possible

        for row in materialized:
            prov, county, _district, town, village, name = [_clean(value) for value in row[:6]]
            if (prov, county) not in county_codes:
                continue
            if _is_zero(town) or not _is_zero(village):
                continue
            normalized = self._strip_parent(name, target)
            if normalized:
                key = (prov, county, town)
                previous = township_names.get(key)
                if previous and previous != normalized:
                    raise CECOpenDataError(
                        f"conflicting township names for {key}: {previous!r} vs {normalized!r}"
                    )
                township_names[key] = normalized

        return {"county_codes": county_codes, "townships": township_names}

    def _build_candidate_map(
        self,
        rows: Iterable[List[str]],
        party_map: Dict[str, str],
        query: DataQuery,
    ) -> Dict[Tuple[str, ...], Dict[str, str]]:
        out: Dict[Tuple[str, ...], Dict[str, str]] = {}
        for row in rows:
            if len(row) < 16:
                continue
            prov, county, district, _town, _village = [_clean(value) for value in row[:5]]
            no = _clean(row[5])
            name = _clean(row[6])
            party_code = _clean(row[7])
            is_vice = _clean(row[15]).upper()
            if not no or not name:
                continue
            if query.election_type == "president" and is_vice == "Y":
                continue
            party = party_map.get(party_code) or ("無黨籍及未經政黨推薦" if party_code == "999" else f"政黨代碼{party_code}")
            out[(prov, county, district, no)] = {"name": name, "party": party, "party_code": party_code}
            out.setdefault((prov, county, no), {"name": name, "party": party, "party_code": party_code})
            if query.election_type == "president":
                out.setdefault((no,), {"name": name, "party": party, "party_code": party_code})
        return out

    @staticmethod
    def _candidate_for(
        candidate_map: Dict[Tuple[str, ...], Dict[str, str]],
        query: DataQuery,
        prov: str,
        county: str,
        district: str,
        candidate_no: str,
    ) -> Optional[Dict[str, str]]:
        if query.election_type == "president":
            return candidate_map.get((candidate_no,))
        return (
            candidate_map.get((prov, county, district, candidate_no))
            or candidate_map.get((prov, county, candidate_no))
        )

    @staticmethod
    def _region_id(prov: str, county: str, town: str) -> str:
        return f"cec:{prov}:{county}:{town}"

    @staticmethod
    def _candidate_id(
        query: DataQuery,
        candidate: Dict[str, str],
        prov: str,
        county: str,
        district: str,
        candidate_no: str,
    ) -> str:
        return (
            f"cec:{query.year}:{query.election_type}:{prov}:{county}:"
            f"{district}:{candidate_no}:{candidate['name']}"
        )
