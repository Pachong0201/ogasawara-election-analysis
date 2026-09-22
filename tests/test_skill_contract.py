import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def read_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def load_yaml(rel: str):
    with (ROOT / rel).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestSkillStructure(unittest.TestCase):
    def test_required_files_exist(self):
        required = [
            "SKILL.md",
            "README.md",
            "requirements.txt",
            "config/source_priority.yaml",
            "config/evidence_grades.yaml",
            "config/analysis_thresholds.yaml",
            "config/knowledge_layers.yaml",
            "config/data_sources.yaml",
            "config/freshness.yaml",
            "config/runtime.yaml",
            "rules/historical_baseline.yaml",
            "rules/data_acquisition.yaml",
            "rules/residual_analysis.yaml",
            "rules/poll_rules.yaml",
            "rules/local_knowledge_rules.yaml",
            "rules/writing_rules.yaml",
            "rules/political_neutrality.yaml",
            "schemas/election_record.yaml",
            "schemas/candidate.yaml",
            "schemas/poll.yaml",
            "schemas/political_claim.yaml",
            "schemas/local_relationship.yaml",
            "schemas/retrieval_lead.yaml",
            "methods/electoral_swing.md",
            "methods/split_ticket.md",
            "methods/candidate_residual.md",
            "methods/spatial_divergence.md",
            "methods/incumbent_transfer.md",
            "methods/third_force.md",
            "runtime/__init__.py",
            "runtime/models.py",
            "runtime/data_readiness.py",
            "runtime/source_registry.py",
            "runtime/cec_open_data.py",
            "runtime/cec_current_candidates.py",
            "runtime/tvbs_poll_center.py",
            "runtime/election_loader.py",
            "runtime/election_normalizer.py",
            "runtime/matrix_builder.py",
            "runtime/metrics.py",
            "runtime/knowledge_loader.py",
            "runtime/host_retrieval.py",
            "runtime/freshness.py",
            "runtime/analysis_context.py",
            "runtime/pipeline.py",
            "runtime/cli.py",
            "tests/test_cec_open_data.py",
            "tests/test_cec_current_candidates.py",
            "tests/test_tvbs_poll_center.py",
            "tests/test_data_readiness.py",
            "tests/test_freshness.py",
            "tests/test_election_loader.py",
            "tests/test_matrix_builder.py",
            "tests/test_metrics.py",
            "tests/test_analysis_context.py",
            "tests/test_pipeline.py",
            "tests/test_host_retrieval.py",
            "examples/yilan/test_cases.yaml",
            "examples/yilan/README.md",
            "tests/README.md",
        ]
        for rel in required:
            with self.subTest(rel=rel):
                self.assertTrue((ROOT / rel).is_file(), f"missing {rel}")

    def test_all_yaml_parse(self):
        yaml_files = sorted(ROOT.rglob("*.yaml")) + sorted(ROOT.rglob("*.yml"))
        self.assertGreaterEqual(len(yaml_files), 10)
        for path in yaml_files:
            with self.subTest(path=str(path.relative_to(ROOT))):
                with path.open(encoding="utf-8") as fh:
                    yaml.safe_load(fh)


class TestCoreContract(unittest.TestCase):
    def test_skill_frontmatter_name(self):
        text = read_text("SKILL.md")
        self.assertIn("name: ogasawara-election-analysis", text)
        self.assertIn("version: 1.2.0", text)

    def test_analysis_path_is_historical_first(self):
        text = read_text("SKILL.md")
        path = "历史基准` → `跨届变化` → `跨层级差异` → `空间异常` → `候选人残差` → `地方知识验证` → `民调校准` → `结构判断"
        self.assertIn(path, text)
        self.assertIn("最新民调` → `直接判断当前选情", text)

    def test_output_sections_exist(self):
        writing = load_yaml("rules/writing_rules.yaml")
        keys = [section["key"] for section in writing["default_output_sections"]]
        expected = [
            "core_judgement",
            "historical_geography",
            "change_since_last",
            "where_change_happened",
            "candidate_local_base",
            "third_force",
            "poll_calibration",
            "structural_factors",
            "watchlist",
        ]
        for key in expected:
            with self.subTest(key=key):
                self.assertIn(key, keys)

    def test_hard_prohibitions_are_complete(self):
        neutrality = load_yaml("rules/political_neutrality.yaml")
        ids = [rule["id"] for rule in neutrality["hard_prohibitions"]]
        for number in range(1, 15):
            self.assertIn(f"NEUTRAL-{number:02d}", ids)
        joined = " ".join(rule["statement"] for rule in neutrality["hard_prohibitions"])
        self.assertIn("个人票", joined)
        self.assertIn("蓝白票", joined)
        self.assertIn("历史派系", joined)
        self.assertIn("胜负概率", joined)

    def test_poll_rules_complete(self):
        poll = load_yaml("rules/poll_rules.yaml")
        ids = [rule["id"] for rule in poll["rules"]]
        for number in range(1, 9):
            self.assertIn(f"POLL-{number:02d}", ids)
        self.assertIn("未决定", " ".join(rule["statement"] for rule in poll["rules"]))

    def test_source_priority_contains_all_grades_and_hard_rule(self):
        source = load_yaml("config/source_priority.yaml")
        for grade in ["A", "B", "C", "D", "E"]:
            self.assertIn(grade, source["grades"])
        joined = " ".join(rule["statement"] for rule in source["hard_rules"])
        self.assertIn("D 级和 E 级资料不得单独形成结构性判断", joined)

    def test_minimum_data_requirements(self):
        thresholds = load_yaml("config/analysis_thresholds.yaml")
        minimum = thresholds["minimum_data"]
        self.assertEqual(minimum["same_election_type_periods"], 3)
        self.assertEqual(minimum["geographic_floor_for_county_mayor"], "township")
        self.assertEqual(minimum["county_mayor_example_for_2026"], [2014, 2018, 2022])
        self.assertEqual(minimum["president_example_for_2026"], [2016, 2020, 2024])

    def test_knowledge_layers_are_separated(self):
        layers = load_yaml("config/knowledge_layers.yaml")
        ids = [layer["id"] for layer in layers["layers"]]
        self.assertEqual(ids, ["L1", "L2", "L3", "L4", "L5"])
        statements = " ".join(rule["statement"] for rule in layers["separation_rules"])
        self.assertIn("历史政治知识与当前地方政治知识必须物理或逻辑分离", statements)
        self.assertIn("历史关系不得自动外推到当前", statements)

    def test_local_knowledge_has_stop_conditions(self):
        local = load_yaml("rules/local_knowledge_rules.yaml")
        conditions = local["stop_conditions"]["conditions"]
        self.assertEqual(len(conditions), 4)
        self.assertTrue(local["stop_conditions"]["all_must_be_met"])
        self.assertIn("当前只能确认票型异常", local["stop_conditions"]["if_not_met"]["output"])

    def test_methods_contain_required_concepts(self):
        required = {
            "methods/electoral_swing.md": ["Swing", "LocalSwing", "乡镇市区"],
            "methods/split_ticket.md": ["SplitTicketResidual", "候选人残差"],
            "methods/candidate_residual.md": ["baseline_method", "个人票"],
            "methods/spatial_divergence.md": ["NeighborDivergence", "派系"],
            "methods/incumbent_transfer.md": ["现任支持不等于接班支持", "接班人"],
            "methods/third_force.md": ["KMT + TPP = 蓝白票", "重新分配"],
        }
        for rel, terms in required.items():
            text = read_text(rel)
            for term in terms:
                with self.subTest(rel=rel, term=term):
                    self.assertIn(term, text)


class TestAcceptanceCases(unittest.TestCase):
    def test_yilan_cases_exist(self):
        data = load_yaml("examples/yilan/test_cases.yaml")
        ids = [case["id"] for case in data["tests"]]
        for expected in ["TEST-A", "TEST-B", "TEST-C"] + [f"CASE-{i}" for i in range(1, 7)]:
            with self.subTest(case=expected):
                self.assertIn(expected, ids)

    def test_case_expectations(self):
        data = load_yaml("examples/yilan/test_cases.yaml")
        cases = {case["id"]: case for case in data["tests"]}
        case2 = cases["CASE-2"]
        self.assertTrue(any("残差" in value for value in case2["pass_criteria"]))
        case3 = cases["CASE-3"]
        self.assertTrue(any("拒绝机械加总" in value for value in case3["pass_criteria"]))
        case4 = cases["CASE-4"]
        self.assertTrue(any("不得直接归因派系" in value for value in case4["pass_criteria"]))
        case5 = cases["CASE-5"]
        self.assertTrue(any("不得直接计算趋势" in value for value in case5["pass_criteria"]))
        case6 = cases["CASE-6"]
        self.assertTrue(any("当前资料重新验证" in value for value in case6["pass_criteria"]))

    def test_yilan_is_test_only_not_dependency(self):
        text = read_text("examples/yilan/README.md")
        self.assertIn("不是 Skill 运行依赖", text)
        skill = read_text("SKILL.md")
        self.assertIn("examples/yilan/` 只作为测试用例", skill)
        readme = read_text("README.md")
        self.assertIn("不是运行依赖", readme)


if __name__ == "__main__":
    unittest.main()
