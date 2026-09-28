"""Runtime candidate scoping, promotion fallback and cache rebuild tests."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from runtime.candidate_records import candidate_is_applicable
from runtime.data_readiness import DataReadinessGate
from runtime.election_loader import ElectionLoader, current_candidates_path
from runtime.models import ElectionTask
from tests.fixtures.helpers import (
    DEFAULT_JURISDICTION,
    REAL_RUNTIME_CONFIG,
    make_task,
    populate_full_repo,
    temp_repo,
)

AS_OF = dt.date(2026, 9, 23)
REPO_ROOT = Path(__file__).resolve().parents[1]


def _promoted(record):
    record.setdefault(
        "promotion_provenance",
        {
            "proposal_id": f"promote-{record.get('candidate_id')}",
            "promoted_at": "2026-09-20T00:00:00+00:00",
            "builder_version": "1.4.0",
        },
    )
    return record


def _mayor(candidate_id="c-mayor", **overrides):
    record = {
        "candidate_id": candidate_id,
        "name": "甲候選人",
        "candidate_name": "甲候選人",
        "election_type": "county_mayor",
        "election_year": 2026,
        "jurisdiction": DEFAULT_JURISDICTION,
        "candidate_status": "registered",
        "registration_date": "2026-08-31",
        "last_verified_at": "2026-09-20",
        "source_grade": "A",
        "independent_source_count": 1,
    }
    record.update(overrides)
    return _promoted(record)


def _write_knowledge_candidates(root: Path, county: str, records):
    path = root / "knowledge" / "local" / county / "candidates.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n",
        encoding="utf-8",
    )
    return path


def test_candidate_is_applicable_fails_closed():
    base = _mayor()
    assert candidate_is_applicable(
        base,
        jurisdiction=DEFAULT_JURISDICTION,
        election_type="county_mayor",
        target_year=2026,
        as_of=AS_OF,
        require_promotion=True,
    )[0]

    councilor = dict(base, election_type="councilor")
    assert candidate_is_applicable(
        councilor,
        jurisdiction=DEFAULT_JURISDICTION,
        election_type="county_mayor",
        target_year=2026,
        as_of=AS_OF,
    )[1] == "wrong_election_type"

    withdrawn = dict(base, candidate_status="withdrawn")
    assert candidate_is_applicable(
        withdrawn,
        jurisdiction=DEFAULT_JURISDICTION,
        election_type="county_mayor",
        target_year=2026,
        as_of=AS_OF,
    )[1] == "inactive_status"

    unknown = dict(base, candidate_status="rumored")
    assert candidate_is_applicable(
        unknown,
        jurisdiction=DEFAULT_JURISDICTION,
        election_type="county_mayor",
        target_year=2026,
        as_of=AS_OF,
    )[1] == "unknown_status"

    unpromoted = {key: value for key, value in base.items() if key != "promotion_provenance"}
    assert candidate_is_applicable(
        unpromoted,
        jurisdiction=DEFAULT_JURISDICTION,
        election_type="county_mayor",
        target_year=2026,
        as_of=AS_OF,
        require_promotion=True,
    )[1] == "not_promoted"

    weak_source = dict(base, source_grade="C", independent_source_count=1)
    assert candidate_is_applicable(
        weak_source,
        jurisdiction=DEFAULT_JURISDICTION,
        election_type="county_mayor",
        target_year=2026,
        as_of=AS_OF,
    )[1] == "unqualified_source"

    future = dict(base, last_verified_at="2026-09-25")
    assert candidate_is_applicable(
        future,
        jurisdiction=DEFAULT_JURISDICTION,
        election_type="county_mayor",
        target_year=2026,
        as_of=AS_OF,
    )[1] == "not_yet_verified"


def test_rebuild_cache_uses_only_usable_mayors_and_preserves_verification_date():
    with temp_repo() as root:
        valid = _mayor("c-valid")
        councilor = _mayor("c-councilor", election_type="councilor")
        withdrawn = _mayor("c-withdrawn", candidate_status="withdrawn")
        weak = _mayor("c-weak", source_grade="C", independent_source_count=1)
        _write_knowledge_candidates(
            root, DEFAULT_JURISDICTION, [valid, councilor, withdrawn, weak]
        )
        loader = ElectionLoader(root, mode="offline")
        result = loader.rebuild_current_candidate_cache(
            DEFAULT_JURISDICTION, "county_mayor", 2026, as_of=AS_OF
        )
        assert result["status"] == "rebuilt"
        assert result["count"] == 1
        cached = json.loads(
            current_candidates_path(root, DEFAULT_JURISDICTION)
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        assert cached["candidate_id"] == "c-valid"
        assert cached["last_verified_at"] == "2026-09-20"
        assert cached["election_type"] == "county_mayor"
        assert "_candidate_origin" not in cached


def test_readiness_reads_promoted_knowledge_when_cache_empty():
    with temp_repo() as root:
        populate_full_repo(root, include_current_candidates=False)
        _write_knowledge_candidates(
            root,
            DEFAULT_JURISDICTION,
            [_mayor("c-1"), _mayor("c-2", name="乙候選人", candidate_name="乙候選人")],
        )
        report = DataReadinessGate(
            root, runtime_config_path=REAL_RUNTIME_CONFIG
        ).check(make_task(), now=AS_OF)
        assert report.status == "READY"
        info = report.required["current_candidate_list"]
        assert info["satisfied"] is True
        assert info["registered_count"] == 2
        assert info["candidate_source_counts"] == {"knowledge_local": 2}
        assert {
            row["election_type"] for row in info["verified_candidates"]
        } == {"county_mayor"}


def test_councilor_profiles_do_not_satisfy_mayor_gate():
    with temp_repo() as root:
        populate_full_repo(root, include_current_candidates=False)
        _write_knowledge_candidates(
            root,
            DEFAULT_JURISDICTION,
            [_mayor("c-councilor", election_type="councilor")],
        )
        report = DataReadinessGate(
            root, runtime_config_path=REAL_RUNTIME_CONFIG
        ).check(make_task(), now=AS_OF)
        assert report.status == "INSUFFICIENT"
        info = report.required["current_candidate_list"]
        assert info["satisfied"] is False
        assert info["verified_count"] == 0
        assert info["excluded_other_election_types"].get("councilor") == 1
        assert any("excluded from this county_mayor check" in w for w in info["warnings"])


def test_taipei_offline_sample_reads_promoted_mayors():
    report = DataReadinessGate(
        REPO_ROOT, runtime_config_path=REPO_ROOT / "config" / "runtime.yaml"
    ).check(
        ElectionTask(
            election_type="county_mayor", target_year=2026, jurisdiction="台北市"
        ),
        now=dt.date(2026, 9, 27),
    )
    assert report.status == "READY"
    info = report.required["current_candidate_list"]
    assert info["satisfied"] is True
    assert info["registered_count"] >= 2
    assert all(
        row["election_type"] == "county_mayor" and row["jurisdiction"] == "台北市"
        for row in info["verified_candidates"]
    )
