import unittest

from runtime.cec_current_candidates import CECCurrentCandidateAdapter
from runtime.data_readiness import DataReadinessGate
from runtime.election_loader import ElectionLoader
from runtime.source_registry import SourceRegistry
from tests.fixtures.helpers import (
    REAL_RUNTIME_CONFIG,
    make_task,
    populate_full_repo,
    temp_repo,
)


PAGE_URL = "https://web.cec.gov.tw/central/article/64733"
COUNTY_PDF = "https://web.cec.gov.tw/api/file/county.pdf"
MUNICIPAL_PDF = "https://web.cec.gov.tw/api/file/municipal.pdf"
COUNTY_COUNCILOR_PDF = "https://web.cec.gov.tw/api/file/county-councilor.pdf"
MUNICIPAL_COUNCILOR_PDF = "https://web.cec.gov.tw/api/file/municipal-councilor.pdf"


def fake_html():
    return f"""
    <html><body>
      <a href="/api/file/municipal.pdf">1-1(115年直轄市長選舉候選人登記彙總表) pdf</a>
      <a href="/api/file/party.pdf">1-2(115年直轄市長選舉政黨推薦候選人登記情形彙總表) pdf</a>
      <a href="/api/file/municipal-councilor.pdf">2-1(115年直轄市議員選舉候選人登記彙總表) pdf</a>
      <a href="/api/file/municipal-councilor-party.pdf">2-2(115年直轄市議員選舉政黨推薦候選人登記情形彙總表) pdf</a>
      <a href="/api/file/county.pdf">3-1(115年縣市長選舉候選人登記彙總表) pdf</a>
      <a href="/api/file/county-party.pdf">3-2(115年縣市長選舉政黨推薦候選人登記情形彙總表) pdf</a>
      <a href="/api/file/county-councilor.pdf">4-1(115年縣市議員選舉候選人登記彙總表) pdf</a>
      <a href="/api/file/county-councilor-party.pdf">4-2(115年縣市議員選舉政黨推薦候選人登記情形彙總表) pdf</a>
    </body></html>
    """.encode("utf-8")


def fake_fetcher(url):
    if url == PAGE_URL:
        return fake_html()
    if url == COUNTY_PDF:
        return b"COUNTY"
    if url == MUNICIPAL_PDF:
        return b"MUNICIPAL"
    if url == COUNTY_COUNCILOR_PDF:
        return b"COUNTY_COUNCILOR"
    if url == MUNICIPAL_COUNCILOR_PDF:
        return b"MUNICIPAL_COUNCILOR"
    raise AssertionError(f"unexpected URL: {url}")


def fake_parser(pdf_bytes):
    header = [["選舉區", "登記日期", "姓名", "推薦之政黨", "備註"]]
    if pdf_bytes == b"COUNTY":
        return [
            header
            + [
                ["新竹縣", "115/09/02", "鄭朝方", "民主進步黨", ""],
                ["新竹縣", "115/09/03", "徐欣瑩", "中國國民黨", ""],
                ["宜蘭縣", "115/09/01", "吳宗憲", "中國國民黨", ""],
                ["宜蘭縣", "115/09/02", "林國\n漳", "民主進步黨", ""],
                ["宜蘭縣", "115/09/04", "楊鉯婷", "無", ""],
            ]
        ]
    if pdf_bytes == b"MUNICIPAL":
        return [
            header
            + [
                ["臺北市", "115/09/01", "甲候選人", "中國國民黨", ""],
                ["高雄市", "115/09/02", "乙候選人", "民主進步黨", ""],
            ]
        ]
    if pdf_bytes == b"MUNICIPAL_COUNCILOR":
        return [
            header
            + [
                ["臺北市第1選舉區", "115/09/01", "甲議員候選人", "中國國民黨", ""],
                ["臺北市第2選舉區", "115/09/02", "乙議員候選人", "民主進步黨", ""],
                ["新北市第1選舉區", "115/09/02", "丙議員候選人", "無", ""],
            ]
        ]
    if pdf_bytes == b"COUNTY_COUNCILOR":
        return [
            header
            + [
                ["新竹縣第1選舉區", "115/09/01", "竹甲", "中國國民黨", ""],
                ["新竹縣第2選舉區", "115/09/03", "竹乙", "民主進步黨", ""],
                ["宜蘭縣第1選舉區", "115/09/04", "宜甲", "無", ""],
            ]
        ]
    return []


class TestCECCurrentCandidateAdapter(unittest.TestCase):
    def adapter(self):
        return CECCurrentCandidateAdapter(
            page_url=PAGE_URL,
            fetcher=fake_fetcher,
            pdf_table_parser=fake_parser,
        )

    def test_parses_official_registration_rows_without_upgrading_status(self):
        result = self.adapter().fetch("宜兰县", "county_mayor", 2026)
        self.assertEqual(result.source_id, "cec_current_candidates")
        self.assertEqual(result.source_grade, "A")
        self.assertEqual(len(result.records), 3)

        by_name = {row["name"]: row for row in result.records}
        self.assertIn("林國漳", by_name)
        self.assertEqual(by_name["吳宗憲"]["candidate_status"], "registered")
        self.assertEqual(by_name["吳宗憲"]["registration_date"], "2026-09-01")
        self.assertEqual(by_name["楊鉯婷"]["recommended_by_party"], "")
        self.assertEqual(by_name["楊鉯婷"]["party"], "未由政黨推薦")
        self.assertNotIn("qualified", {row["candidate_status"] for row in result.records})

    def test_filters_municipality_and_county_pdfs_by_target_region(self):
        taipei = self.adapter().fetch("台北市", "county_mayor", 2026)
        self.assertEqual([row["name"] for row in taipei.records], ["甲候選人"])
        self.assertEqual(taipei.records[0]["official_jurisdiction"], "臺北市")

    def test_parses_councilor_registration_rows_by_county(self):
        taipei = self.adapter().fetch("台北市", "councilor", 2026)
        self.assertEqual(len(taipei.records), 2)
        self.assertEqual(
            {row["electoral_district"] for row in taipei.records},
            {"臺北市第1選舉區", "臺北市第2選舉區"},
        )
        self.assertTrue(all(row["election_type"] == "councilor" for row in taipei.records))
        self.assertTrue(all(row["candidate_status"] == "registered" for row in taipei.records))

        hsinchu = self.adapter().fetch("新竹縣", "councilor", 2026)
        self.assertEqual({row["name"] for row in hsinchu.records}, {"竹甲", "竹乙"})
        self.assertEqual(
            {row["electoral_district"] for row in hsinchu.records},
            {"新竹縣第1選舉區", "新竹縣第2選舉區"},
        )

    def test_loader_refreshes_and_persists_candidate_cache(self):
        with temp_repo() as root:
            registry = SourceRegistry(candidate_adapters=[self.adapter()])
            loader = ElectionLoader(root, source_registry=registry, mode="online")
            result = loader.refresh_current_candidates("宜兰县", "county_mayor", 2026)

            self.assertEqual(result.status, "filled")
            self.assertTrue(result.persisted)
            cached = loader.load_current_candidates("宜兰县")
            self.assertEqual(len(cached), 3)
            self.assertTrue(all(row["source_grade"] == "A" for row in cached))
            self.assertTrue(all(row["candidate_status"] == "registered" for row in cached))

    def test_readiness_prepare_refreshes_missing_current_candidates(self):
        with temp_repo() as root:
            populate_full_repo(root, include_current_candidates=False)
            registry = SourceRegistry(candidate_adapters=[
                CECCurrentCandidateAdapter(
                    page_url=PAGE_URL,
                    fetcher=fake_fetcher,
                    pdf_table_parser=lambda data: [
                        [["選舉區", "登記日期", "姓名", "推薦之政黨", "備註"],
                         ["新竹縣", "115/09/02", "甲候選人", "民主進步黨", ""],
                         ["新竹縣", "115/09/03", "乙候選人", "中國國民黨", ""]]
                    ] if data == b"COUNTY" else []
                )
            ])
            loader = ElectionLoader(root, source_registry=registry, mode="online")
            gate = DataReadinessGate(root, runtime_config_path=REAL_RUNTIME_CONFIG)

            report = gate.prepare(make_task(), loader=loader, allow_online=True)
            self.assertEqual(report.status, "READY")
            self.assertTrue(report.required["current_candidate_list"]["satisfied"])
            self.assertEqual(report.required["current_candidate_list"]["registered_count"], 2)


if __name__ == "__main__":
    unittest.main()
