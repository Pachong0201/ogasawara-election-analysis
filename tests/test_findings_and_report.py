import tempfile
import unittest
import zipfile
from pathlib import Path

from runtime.cec_open_data import open_cp950_zip
from runtime.findings import FindingsBuilder
from runtime.pipeline import AnalysisPipeline
from runtime.report_validator import ReportValidator
from runtime.report_writer import ReportWriter, build_report_brief
from tests.fixtures.helpers import REPO_ROOT, make_task, populate_full_repo, temp_repo


def rec(etype, year, region, name, party, share, valid=10000, turnout=0.6):
    return {
        "election_type": etype,
        "election_year": year,
        "jurisdiction": region,
        "candidate_name": name,
        "party": party,
        "vote_share": share,
        "votes": round(share * valid),
        "valid_votes": valid,
        "turnout": turnout,
        "source_id": "cec_open_data",
        "source_grade": "A",
    }


def mayor_pair(year, a_share, b_share, a_region_share=None, valid_b=10000):
    return [
        rec("county_mayor", year, "甲鄉", f"A{year}", "甲黨", a_share if a_region_share is None else a_region_share),
        rec("county_mayor", year, "甲鄉", f"B{year}", "乙黨", 1 - (a_share if a_region_share is None else a_region_share)),
        rec("county_mayor", year, "乙鄉", f"A{year}", "甲黨", b_share, valid=valid_b),
        rec("county_mayor", year, "乙鄉", f"B{year}", "乙黨", 1 - b_share, valid=valid_b),
    ]


class TestMetricLogic(unittest.TestCase):
    def setUp(self):
        self.pipeline = AnalysisPipeline(REPO_ROOT, mode="offline")

    def test_uniform_countywide_swing_is_not_a_local_anomaly(self):
        records = mayor_pair(2018, 0.40, 0.50) + mayor_pair(2022, 0.55, 0.65)
        raw, local = self.pipeline._electoral_swings(records)
        self.assertTrue(raw)
        self.assertTrue(all(r["threshold_status"] == "descriptive" for r in raw))
        self.assertTrue(all(not r["local_explanation_required"] for r in local))
        row = next(r for r in local if r["party"] == "甲黨")
        self.assertAlmostEqual(row["county_swing"], 0.15, places=6)

    def test_local_swing_triggers_only_on_deviation_from_county(self):
        records = mayor_pair(2018, 0.40, 0.50) + mayor_pair(2022, 0.60, 0.50)
        _, local = self.pipeline._electoral_swings(records)
        flagged = {(r["region"], r["party"]) for r in local if r["local_explanation_required"]}
        self.assertIn(("甲鄉", "甲黨"), flagged)

    def test_independents_are_not_pooled_across_elections(self):
        records = [
            rec("county_mayor", 2018, "甲鄉", "張三", "無黨籍及未經政黨推薦", 0.20),
            rec("county_mayor", 2018, "乙鄉", "張三", "無黨籍及未經政黨推薦", 0.20),
            rec("county_mayor", 2022, "甲鄉", "李四", "無黨籍及未經政黨推薦", 0.40),
            rec("county_mayor", 2022, "乙鄉", "李四", "無黨籍及未經政黨推薦", 0.40),
        ]
        raw, local = self.pipeline._electoral_swings(records)
        self.assertEqual(raw, [])
        self.assertEqual(local, [])

    def test_spatial_anomaly_region_is_real_jurisdiction(self):
        rows = self.pipeline._spatial_anomalies(make_task(jurisdiction="測試縣"), mayor_pair(2022, 0.30, 0.70))
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["region"], "測試縣")
            self.assertNotIn(":", row["region"])
            self.assertTrue(row["details"]["weighted"])

    def test_candidate_residual_is_relative_to_county(self):
        mayor = mayor_pair(2022, 0.60, 0.50)
        pres = []
        for year in (2020, 2024):
            for region, share in (("甲鄉", 0.40), ("乙鄉", 0.40)):
                pres.append(rec("president", year, region, f"P{year}", "甲黨", share))
                pres.append(rec("president", year, region, f"Q{year}", "乙黨", 1 - share))
        rows = [r for r in self.pipeline._candidate_residuals(make_task(), mayor, pres) if r["party"] == "甲黨"]
        by_region = {r["region"]: r for r in rows}
        self.assertAlmostEqual(by_region["甲鄉"]["county_residual"], 0.15, places=6)
        self.assertAlmostEqual(by_region["甲鄉"]["relative_residual"], 0.05, places=6)
        self.assertAlmostEqual(by_region["乙鄉"]["relative_residual"], -0.05, places=6)
        self.assertAlmostEqual(by_region["甲鄉"]["absolute_residual"], 0.20, places=6)

    def test_split_ticket_with_different_electorates_is_not_comparable(self):
        pres = [rec("president", 2024, r, "P", "甲黨", 0.5, valid=10000) for r in ("甲鄉", "乙鄉")]
        leg = [
            rec("regional_legislator", 2024, "甲鄉", "L", "甲黨", 0.5, valid=10000),
            rec("regional_legislator", 2024, "乙鄉", "L", "甲黨", 0.9, valid=3000),
        ]
        rows = {r["region"]: r for r in self.pipeline._split_ticket(pres, leg)}
        self.assertTrue(rows["乙鄉"]["electorate_mismatch"])
        self.assertFalse(rows["乙鄉"]["local_explanation_required"])

    def test_small_sample_caps_strong_trigger(self):
        records = mayor_pair(2018, 0.40, 0.40, valid_b=1000) + mayor_pair(2022, 0.40, 0.80, valid_b=1000)
        _, local = self.pipeline._electoral_swings(records)
        row = next(r for r in local if r["region"] == "乙鄉" and r["party"] == "甲黨")
        self.assertTrue(row["small_sample"])
        self.assertEqual(row["threshold_status"], "observe")


class TestZipCompat(unittest.TestCase):
    def test_open_cp950_zip_reads_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("voteData/縣市長/elbase.csv", "x")
            with open_cp950_zip(path) as zf:
                self.assertIn("voteData/縣市長/elbase.csv", zf.namelist())
                self.assertEqual(zf.read("voteData/縣市長/elbase.csv"), b"x")


class TestFindingsAndReport(unittest.TestCase):
    def _brief(self):
        with temp_repo() as root:
            populate_full_repo(root)
            context = AnalysisPipeline(root, mode="offline").run(make_task(), allow_online=False)
            return context, build_report_brief(context)

    def test_pipeline_emits_citable_findings(self):
        context, brief = self._brief()
        findings = context.analysis_context["findings"]
        self.assertTrue(findings["findings"])
        source_ids = {s["id"] for s in brief["sources"]}
        for finding in findings["findings"]:
            self.assertRegex(finding["finding_id"], r"^F-\d{3}$")
            self.assertTrue(set(finding["sources"]) <= source_ids, finding)
            if finding["level"] == "township":
                self.assertEqual(finding["status"], "needs_local_knowledge")
                self.assertTrue(finding["competing_explanations"])
                self.assertIn(finding["region"], finding["research_question"])
        self.assertTrue(findings["storylines"])
        for question in context.analysis_context["local_knowledge"]["research_questions"]:
            self.assertNotRegex(question, r"出现 \w+ 异常")

    def test_findings_builder_flags_poll_scenario_mismatch(self):
        task = make_task()
        builder = FindingsBuilder()
        result = builder.build(
            task,
            {"county_mayor": mayor_pair(2022, 0.5, 0.5)},
            {},
            campaign_state={"as_of": "2026-09-01T00:00:00+00:00"},
            current_candidates=[{"candidate_name": "甲", "party": "甲黨", "source_grade": "A"}],
            polls=[{"poll_id": "p1", "pollster": "X", "freshness_status": "stale", "source_grade": "C",
                    "candidate_support": [{"candidate": "甲", "support": 0.3}, {"candidate": "丙", "support": 0.1}]}],
        )
        kinds = {f["kind"] for f in result["findings"]}
        self.assertIn("poll_field_mismatch", kinds)
        self.assertIn("poll_stale", kinds)

    def test_prompt_contains_rules_brief_and_exemplar(self):
        _, brief = self._brief()
        messages = ReportWriter(repo_root=REPO_ROOT).build_messages(brief)
        self.assertIn("段落公式", messages["system"])
        self.assertIn("核心判断", messages["user"])
        self.assertIn("甲鄉", messages["user"])
        self.assertIn(brief["as_of"], messages["user"])


BRIEF = {
    "as_of": "2026-09-01",
    "findings": [
        {"finding_id": "F-001", "statement": "甲黨得票率52.3%", "evidence_grade": "A", "numbers": [0.523, 0.083]},
        {"finding_id": "F-002", "statement": "阵营称", "evidence_grade": "D", "numbers": []},
    ],
    "sources": [{"id": "S-cec-county_mayor-2022", "source_grade": "A"}],
    "campaign": {"fresh_polls": []},
    "unknowns": ["local knowledge insufficient"],
}
CORE = (
    "截至 2026-09-01，这场选战仍处于两强对峙、议题尚未成形的阶段，最近三十天没有可以验证的新事件，"
    "也没有新鲜民调能够校准当前局势。甲黨上一届以52.3%胜出[F-001]，但其优势集中在少数乡镇，"
    "地方组织是否延续仍需观察，现有资料尚不能确定其原因。真正值得追问的是：候选人更替之后，"
    "原本的地方网络会不会随之移转，而对手是否能够在中间选民集中的城区找到突破口，这决定了"
    "下一阶段的竞争结构会不会改变，也决定了历史结构还能解释多少当前选情。"
)
GOOD = f"# 报告\n\n截至 2026-09-01\n\n## 核心判断\n\n{CORE}\n\n## 四、变化主要发生在哪里\n\n甲鄉的落差为8.3个百分点[F-001]。\n"


class TestReportValidator(unittest.TestCase):
    def setUp(self):
        self.validator = ReportValidator()

    def codes(self, text):
        return {i.code for i in self.validator.validate(text, BRIEF).issues if i.severity == "error"}

    def test_good_report_passes(self):
        result = self.validator.validate(GOOD, BRIEF)
        self.assertTrue(result.passed, result.to_dict())

    def test_missing_as_of(self):
        self.assertIn("missing_as_of", self.codes(GOOD.replace("2026-09-01", "九月")))

    def test_banned_expressions(self):
        self.assertIn("banned_expression", self.codes(GOOD + "\n某候选人胜率偏高，且有个人票[F-001]。\n"))

    def test_unverified_and_uncited_numbers(self):
        codes = self.codes(GOOD + "\n乙鄉的落差为17.9%。\n")
        self.assertIn("unverified_number", codes)
        self.assertIn("uncited_numbers", codes)

    def test_unknown_citation_and_weak_sources(self):
        codes = self.codes(GOOD + "\n这反映组织转向[F-099]。\n\n这说明派系动员[F-002]。\n")
        self.assertIn("unknown_citation", codes)
        self.assertIn("weak_sources_only", codes)

    def test_stale_poll_presented_as_current(self):
        codes = self.codes(GOOD + "\n## 七、当前民调是否验证选战变化\n\n甲黨支持度52.3%[F-001]。\n")
        self.assertIn("stale_poll_as_current", codes)
        ok = self.codes(GOOD + "\n## 七、当前民调是否验证选战变化\n\n三月的过期民调显示甲黨52.3%[F-001]。\n")
        self.assertNotIn("stale_poll_as_current", ok)

    def test_core_opening_and_length(self):
        codes = self.codes(GOOD.replace(CORE, "2022年甲黨胜出[F-001]。"))
        self.assertIn("core_opening", codes)
        self.assertIn("core_length", codes)

    def test_missing_unknowns(self):
        text = GOOD.replace("现有资料尚不能确定其原因", "原因明确")
        self.assertIn("missing_unknowns", self.codes(text))


if __name__ == "__main__":
    unittest.main()
