from runtime.current_legislator_materializer import CurrentLegislatorMaterializer
from runtime.election_loader import election_file_path, load_jsonl, write_jsonl
from runtime.ly_current_legislators import LYCurrentLegislatorAdapter, county_from_constituency


LIST_URL = "https://www.ly.gov.tw/Pages/List.aspx?nodeid=109"
A_URL = "https://www.ly.gov.tw/Pages/List.aspx?nodeid=1001"
B_URL = "https://www.ly.gov.tw/Pages/List.aspx?nodeid=1002"
LEFT_URL = "https://www.ly.gov.tw/Pages/List.aspx?nodeid=1999"


def fake_fetch(url):
    if url == LIST_URL:
        return """
        <html><body>
        <h2><span>第11屆</span><span>立法委員名單</span></h2>
        <a href="/Pages/List.aspx?nodeid=1001">
          <img alt="甲委員照片"><img alt="中國國民黨徽章"><span>甲</span>
        </a>
        <a href="/Pages/List.aspx?nodeid=1002"><img alt="乙委員照片"></a>
        <h2>離職 立法委員名單</h2>
        <a href="/Pages/List.aspx?nodeid=1999">離職甲</a>
        </body></html>
        """
    if url == A_URL:
        return """
        <html><body><h3>甲委員</h3>
        <div>屆別：第 11 屆</div><div>黨籍：中國國民黨</div>
        <div>選區：宜蘭縣第1選舉區</div><div>到職日期：113年2月1日</div>
        </body></html>
        """
    if url == B_URL:
        return """
        <html><body><h3>乙委員</h3>
        <div>屆別：第 11 屆</div><div>黨籍：民主進步黨</div>
        <div>選區：全國不分區及僑居國外國民</div><div>到職日期：113年2月1日</div>
        </body></html>
        """
    raise AssertionError(url)


def test_current_legislator_adapter_keeps_current_geographic_members_only():
    adapter = LYCurrentLegislatorAdapter(page_url=LIST_URL, fetcher=fake_fetch)
    result = adapter.fetch_all()

    assert len(result["records"]) == 1
    assert result["records"][0]["name"] == "甲"
    assert result["records"][0]["county"] == "宜蘭縣"
    assert result["records"][0]["onboard_date"] == "2024-02-01"
    assert len(result["unassigned"]) == 1
    assert result["unassigned"][0]["name"] == "乙"
    assert result["failure_count"] == 0


def test_materializes_current_legislator_office_relationship(tmp_path):
    election_path = election_file_path(
        tmp_path, "regional_legislator", 2024, "宜蘭縣"
    )
    write_jsonl(
        election_path,
        [
            {
                "candidate_name": "甲",
                "party": "中國國民黨",
                "votes": 600,
                "source": "https://data.gov.tw/dataset/13119",
                "source_reference": "https://data.cec.gov.tw/votedata.zip",
                "cec_codes": {"election_district": "01"},
            },
            {
                "candidate_name": "另一候選人",
                "party": "民主進步黨",
                "votes": 400,
                "source": "https://data.gov.tw/dataset/13119",
                "source_reference": "https://data.cec.gov.tw/votedata.zip",
                "cec_codes": {"election_district": "01"},
            },
        ],
    )
    adapter = LYCurrentLegislatorAdapter(page_url=LIST_URL, fetcher=fake_fetch)
    materializer = CurrentLegislatorMaterializer(tmp_path, adapter=adapter)
    result = materializer.materialize(apply=True)

    assert result["member_count"] == 1
    assert result["promoted_count"] == 1
    path = tmp_path / "knowledge" / "local" / "宜蘭縣" / "relationships.jsonl"
    rows = load_jsonl(path)
    assert len(rows) == 1
    assert rows[0]["subject"] == "甲"
    assert rows[0]["relationship_type"] == "office_holding"
    assert rows[0]["electoral_district"] == "宜蘭縣第1選舉區"
    assert rows[0]["current_status"] == "active_verified"
    assert rows[0]["source_grade"] == "A"
    assert rows[0]["independent_source_count"] == 2


def test_county_mapping_normalizes_tai_character():
    assert county_from_constituency("臺北市第3選舉區") == "台北市"


def test_member_link_parser_does_not_merge_party_badge_alt_text():
    links = LYCurrentLegislatorAdapter(page_url=LIST_URL, fetcher=fake_fetch).member_links()
    assert links[0] == (A_URL, "甲")


def test_materializer_prunes_only_stale_managed_legislator_rows(tmp_path):
    relationship_path = tmp_path / "knowledge" / "local" / "宜蘭縣" / "relationships.jsonl"
    write_jsonl(
        relationship_path,
        [
            {
                "relationship_id": "ly11-office-宜蘭縣-stale",
                "subject": "甲 中國國民黨徽章 甲",
                "object": "立法院第11屆立法委員",
                "relationship_type": "office_holding",
            },
            {
                "relationship_id": "manual-office-record",
                "subject": "保留人物",
                "object": "立法院第11屆立法委員",
                "relationship_type": "office_holding",
            },
        ],
    )
    materializer = CurrentLegislatorMaterializer(tmp_path, adapter=LYCurrentLegislatorAdapter(
        page_url=LIST_URL, fetcher=fake_fetch
    ))
    removed = materializer._reconcile_managed_relationships("宜蘭縣", {"ly11-office-宜蘭縣-甲"})
    assert removed == 1
    rows = load_jsonl(relationship_path)
    assert [row["relationship_id"] for row in rows] == ["manual-office-record"]
