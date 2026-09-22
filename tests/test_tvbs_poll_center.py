import datetime as dt
import unittest

from runtime.election_loader import ElectionLoader
from runtime.freshness import evaluate_records
from runtime.source_registry import SourceRegistry
from runtime.tvbs_poll_center import TVBSPollCenterAdapter
from tests.fixtures.helpers import temp_repo


INDEX_URL = "https://www.tvbs.com.tw/poll-center"
PDF_URL = "https://www-asset.tvbs.com.tw/poll_center/2026/20260323/fixture.pdf"

INDEX_HTML = f"""
<table>
<tr>
  <td>115年03月19日</td>
  <td><a href="{PDF_URL}">2026宜蘭縣長選情民調</a></td>
</tr>
<tr>
  <td>115年03月12日</td>
  <td><a href="https://www-asset.tvbs.com.tw/poll_center/2026/20260312/tainan.pdf">2026台南市長選情民調</a></td>
</tr>
</table>
""".encode("utf-8")

REPORT_TEXT = """
國、民兩黨人選提名後，宜蘭縣長選情。
今年底的宜蘭縣長選舉，有83%宜蘭縣民表示會去投票。
在藍綠白三方皆推派人選的情況下，民進黨林國漳支持度為33%，
國民黨吳宗憲支持度為28%，兩人差距5個百分點，
而民眾黨陳琬惠則獲得8%表態支持，另外31%尚未決定支持對象。
交叉分析顯示不同年齡及地區受訪者的支持分布。
本次調查是TVBS民意調查中心於115年3月13日至19日晚間18：30至22：00進行的調查，
共接觸1,221位20歲以上宜蘭縣民，其中拒訪為179位，拒訪率為14.7%，
最後成功訪問有效樣本1,042位，在95%的信心水準下，
抽樣誤差為±3.0個百分點以內。
抽樣方法採用市內電話號碼後四碼隨機抽樣，人員電話訪問，
所有資料並依母體性別、年齡、地區、教育程度等變項進行統計加權處理。
調查經費來源為TVBS。
"""


def fetcher(url):
    if url == INDEX_URL:
        return INDEX_HTML
    if url == PDF_URL:
        return b"%PDF-fixture"
    raise AssertionError(f"unexpected URL {url}")


class TestTVBSPollCenterAdapter(unittest.TestCase):
    def adapter(self, text=REPORT_TEXT):
        return TVBSPollCenterAdapter(
            index_url=INDEX_URL,
            fetcher=fetcher,
            pdf_text_parser=lambda _data: text,
        )

    def test_parses_primary_report_methodology_and_support(self):
        result = self.adapter().fetch("宜兰县", "county_mayor", 2026)
        self.assertEqual(result.source_id, "tvbs_poll_center")
        self.assertEqual(result.source_grade, "C")
        self.assertEqual(len(result.records), 1)

        poll = result.records[0]
        self.assertEqual(poll["pollster"], "TVBS民意調查中心")
        self.assertEqual(poll["commissioner"], "self")
        self.assertEqual(poll["method"], "telephone_landline")
        self.assertEqual(poll["sample_size"], 1042)
        self.assertEqual(poll["field_start"], "2026-03-13")
        self.assertEqual(poll["field_end"], "2026-03-19")
        self.assertEqual(poll["publish_date"], "2026-03-23")
        self.assertEqual(poll["moe"], 3.0)
        self.assertTrue(poll["moe_applicable"])
        self.assertEqual(poll["undecided"], 0.31)
        self.assertEqual(poll["scenario"], "three_candidate")
        self.assertIn("後四碼隨機抽樣", poll["sampling"])
        self.assertIn("教育程度", poll["weighting"])
        self.assertIn("20歲以上宜蘭縣民", poll["sample_frame"])
        self.assertFalse(poll["question_wording_is_verbatim"])
        self.assertTrue(poll["cross_tabs_available"])

        support = {item["candidate"]: item["support"] for item in poll["candidate_support"]}
        self.assertEqual(support["林國漳"], 0.33)
        self.assertEqual(support["吳宗憲"], 0.28)
        self.assertEqual(support["陳琬惠"], 0.08)

    def test_fails_closed_when_method_metadata_is_missing(self):
        text = REPORT_TEXT.replace(
            "所有資料並依母體性別、年齡、地區、教育程度等變項進行統計加權處理。",
            "",
        )
        result = self.adapter(text=text).fetch("宜兰县", "county_mayor", 2026)
        self.assertEqual(result.records, [])
        self.assertTrue(any("missing required poll metadata" in warning for warning in result.warnings))

    def test_loader_refreshes_valid_poll_cache(self):
        with temp_repo() as root:
            registry = SourceRegistry(poll_adapters=[self.adapter()])
            loader = ElectionLoader(root, source_registry=registry, mode="online")
            refreshed = loader.refresh_polls("宜兰县", "county_mayor", 2026)
            self.assertEqual(refreshed.status, "filled")
            self.assertTrue(refreshed.persisted)

            loaded = loader.load_polls("宜兰县")
            self.assertEqual(len(loaded["records"]), 1)
            self.assertEqual(len(loaded["invalid"]), 0)

    def test_reverified_old_poll_remains_stale(self):
        result = self.adapter().fetch("宜兰县", "county_mayor", 2026)
        status = evaluate_records(
            result.records,
            kind="poll",
            now=dt.date(2026, 9, 22),
        )
        self.assertEqual(status["fresh"], 0)
        self.assertEqual(status["stale"], 1)

    def test_real_config_registers_poll_adapter(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[1]
        registry = SourceRegistry(repo_root / "config" / "data_sources.yaml")
        ids = [item["source_id"] for item in registry.metadata()["registered_poll_adapters"]]
        self.assertIn("tvbs_poll_center", ids)


if __name__ == "__main__":
    unittest.main()
