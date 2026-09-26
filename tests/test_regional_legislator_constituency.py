from runtime.election_normalizer import validate_records
from runtime.matrix_builder import build_historical_matrix


def _record(district, candidate, votes):
    return {
        "record_id": f"r-{district}-{candidate}",
        "election_type": "regional_legislator",
        "election_year": 2024,
        "jurisdiction": "板橋區",
        "parent_jurisdiction": "新北市",
        "level": "township_district",
        "candidate_name": candidate,
        "party": "測試政黨",
        "votes": votes,
        "valid_votes": 100,
        "vote_share": votes / 100,
        "turnout": 0.7,
        "source": "cec_open_data",
        "source_grade": "A",
        "boundary_version": "fixture-v1",
        "cec_codes": {"election_district": district},
    }


def test_regional_legislator_split_districts_validate_as_separate_contests():
    records = [
        _record("01", "甲", 40),
        _record("01", "乙", 60),
        _record("02", "丙", 45),
        _record("02", "丁", 55),
    ]
    report = validate_records(records)
    assert report.passed, [issue.message for issue in report.issues]
    assert not any(issue.code == "AREA_VOTE_TOTAL_MISMATCH" for issue in report.issues)


def test_regional_legislator_matrix_preserves_constituency_dimension():
    records = [
        _record("01", "甲", 40),
        _record("01", "乙", 60),
        _record("02", "丙", 45),
        _record("02", "丁", 55),
    ]
    matrix = build_historical_matrix(records)
    assert "板橋區｜立委選區01" in matrix["regions"]
    assert "板橋區｜立委選區02" in matrix["regions"]
    assert matrix["regions"]["板橋區｜立委選區01"]["2024"]["甲"] == 0.4
    assert matrix["regions"]["板橋區｜立委選區02"]["2024"]["丙"] == 0.45
