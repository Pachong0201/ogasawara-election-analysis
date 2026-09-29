"""Post-generation checks for LLM-written election reports.

The validator is deliberately mechanical: it verifies what can be verified
from the report brief (dates, numbers, citations, banned expressions, poll
freshness) and reports issues the writer must fix in the next round.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import yaml

PERCENT_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*(?:%|％|个百分点|個百分點|pp)")
CITATION_RE = re.compile(r"\[((?:F|S)-[^\[\]\s]+)\]")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*)$")
INTERPRETIVE_RE = re.compile(r"说明|說明|意味着|意味著|显示出|顯示出|反映|折射|代表着|代表著|暗示")
STALE_MARKER_RE = re.compile(r"过期|過期|时点|時點|当时|當時|已失效|不能校准|不能校準|stale")
CORE_HEADING_RE = re.compile(r"核心判断|核心判斷")
POLL_HEADING_RE = re.compile(r"民调|民調")
BAD_OPENING_RE = re.compile(r"^(\d{4}|\d|根据|根據|据|據|民调|民調|在\d{4}|回顾|回顧|自从|自從)")
UNKNOWN_MARKER_RE = re.compile(r"unknown|尚不能确定|尚不能確定|资料不足|資料不足|证据不足|證據不足|无法确认|無法確認|尚无法|尚無法")
DEFAULT_RULES = Path(__file__).resolve().parents[1] / "rules" / "writing_rules.yaml"


@dataclass
class ValidationIssue:
    code: str
    severity: str
    message: str
    excerpt: str = ""


@dataclass
class ValidationResult:
    passed: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"passed": self.passed, "issues": [asdict(i) for i in self.issues], "stats": self.stats}

    def feedback(self) -> str:
        lines = [f"- [{i.severity}] {i.code}: {i.message}" + (f"（原文：{i.excerpt}）" if i.excerpt else "") for i in self.issues]
        return "\n".join(lines)


def _plain_length(text: str) -> int:
    text = CITATION_RE.sub("", text)
    text = re.sub(r"[\s*_`>#-]", "", text)
    return len(text)


def _paragraphs(text: str) -> List[Dict[str, str]]:
    """Split Markdown into paragraphs tagged with their current section heading."""
    result: List[Dict[str, str]] = []
    heading = ""
    buffer: List[str] = []

    def flush() -> None:
        if buffer:
            body = "\n".join(buffer).strip()
            if body:
                result.append({"heading": heading, "text": body})
            buffer.clear()

    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush()
            heading = match.group(1).strip()
        elif not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    return result


def _numbers_in(values: Iterable[Any]) -> Set[float]:
    found: Set[float] = set()
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            v = float(value)
            found.add(round(abs(v) * 100, 1) if abs(v) <= 1.0 else round(abs(v), 1))
            found.add(round(abs(v), 1))
    return found


def _walk_numbers(obj: Any) -> List[Any]:
    out: List[Any] = []
    if isinstance(obj, dict):
        for value in obj.values():
            out.extend(_walk_numbers(value))
    elif isinstance(obj, list):
        for value in obj:
            out.extend(_walk_numbers(value))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(obj)
    elif isinstance(obj, str):
        out.extend(float(m) for m in re.findall(r"(?<![\d.])(\d{1,3}\.\d)(?=%|个百分点|個百分點)", obj))
    return out


class ReportValidator:
    def __init__(self, rules_path: Optional[Path] = None):
        path = Path(rules_path) if rules_path else DEFAULT_RULES
        with path.open(encoding="utf-8") as fh:
            rules = yaml.safe_load(fh) or {}
        self.banned = [
            (re.compile(item["pattern"]), item.get("reason", "")) for item in rules.get("banned_patterns", [])
        ]
        contract = rules.get("report_contract", {})
        self.tolerance = float(contract.get("percentage_tolerance_pp", 0.15))
        self.max_numbers = int(contract.get("max_core_numbers_per_paragraph", 3))
        core = next((s for s in rules.get("default_output_sections", []) if s.get("key") == "core_judgement"), {})
        low, _, high = str(core.get("length", "150-250字")).rstrip("字").partition("-")
        self.core_min, self.core_max = int(low or 150), int(high or 250)

    def validate(self, report: str, brief: Dict[str, Any]) -> ValidationResult:
        issues: List[ValidationIssue] = []
        paragraphs = _paragraphs(report)
        finding_ids = {f["finding_id"] for f in brief.get("findings", [])}
        source_grades = {s["id"]: s.get("source_grade") for s in brief.get("sources", [])}
        finding_grades = {f["finding_id"]: f.get("evidence_grade") for f in brief.get("findings", [])}
        allowed = _numbers_in(_walk_numbers(brief))
        fresh_polls = brief.get("campaign", {}).get("fresh_polls", [])

        as_of = str(brief.get("as_of") or "")
        if as_of and as_of != "unknown" and as_of not in report:
            issues.append(ValidationIssue("missing_as_of", "error", f"报告必须写明 as_of（{as_of}）"))

        for pattern, reason in self.banned:
            for match in pattern.finditer(report):
                start = max(0, match.start() - 12)
                issues.append(ValidationIssue("banned_expression", "error", reason, report[start: match.end() + 12]))

        core = [p for p in paragraphs if CORE_HEADING_RE.search(p["heading"])]
        if not core:
            issues.append(ValidationIssue("missing_core_judgement", "error", "缺少“核心判断”章节"))
        else:
            body = "\n".join(p["text"] for p in core)
            length = _plain_length(body)
            if not self.core_min <= length <= self.core_max:
                issues.append(ValidationIssue(
                    "core_length", "error", f"核心判断应为{self.core_min}–{self.core_max}字，当前约{length}字"
                ))
            first = CITATION_RE.sub("", core[0]["text"]).lstrip("*_ ")
            if BAD_OPENING_RE.match(first):
                issues.append(ValidationIssue(
                    "core_opening", "error", "核心判断首句不得从历史年份、单一民调数字或“根据……”开始", first[:30]
                ))

        cited_all: Set[str] = set()
        for para in paragraphs:
            text = para["text"]
            cited = set(CITATION_RE.findall(text))
            cited_all |= cited
            for cid in sorted(cited):
                if cid not in finding_ids and cid not in source_grades:
                    issues.append(ValidationIssue("unknown_citation", "error", f"引用了不存在的编号 {cid}", text[:40]))
            numbers = [float(m) for m in PERCENT_RE.findall(text)]
            if numbers and not cited:
                issues.append(ValidationIssue("uncited_numbers", "error", "含百分比的段落必须引用 finding 或来源", text[:40]))
            if INTERPRETIVE_RE.search(text) and not cited:
                issues.append(ValidationIssue("uncited_interpretation", "error", "解释性论断必须带 finding 引用", text[:40]))
            for number in numbers:
                if not any(abs(number - a) <= self.tolerance for a in allowed):
                    issues.append(ValidationIssue(
                        "unverified_number", "error", f"数字 {number} 在 findings/brief 中找不到（容差 {self.tolerance}）", text[:40]
                    ))
            if len(set(numbers)) > self.max_numbers + 2:
                issues.append(ValidationIssue(
                    "number_dense", "warning", f"段落含{len(set(numbers))}个百分比数字，建议每段 2–3 个核心数字", text[:40]
                ))
            grades = [source_grades.get(c) or finding_grades.get(c) for c in cited]
            if cited and all(g in {"D", "E"} for g in grades):
                issues.append(ValidationIssue("weak_sources_only", "error", "D/E 级来源不得单独支撑判断", text[:40]))
            if not fresh_polls and POLL_HEADING_RE.search(para["heading"]) and numbers and not STALE_MARKER_RE.search(text):
                issues.append(ValidationIssue(
                    "stale_poll_as_current", "error", "当前没有新鲜民调；引用旧民调数字时必须标明其为过期/时点证据", text[:40]
                ))
        if not fresh_polls and re.search(r"最新民调|最新民調", report):
            issues.append(ValidationIssue("stale_poll_as_current", "error", "没有新鲜民调，不得称“最新民调”"))

        if brief.get("unknowns") and not UNKNOWN_MARKER_RE.search(report):
            issues.append(ValidationIssue("missing_unknowns", "error", "brief 中有 unknown 项，报告必须说明哪些问题尚不能确定"))

        errors = [i for i in issues if i.severity == "error"]
        return ValidationResult(
            passed=not errors,
            issues=issues,
            stats={
                "paragraphs": len(paragraphs),
                "citations": sorted(cited_all),
                "errors": len(errors),
                "warnings": len(issues) - len(errors),
            },
        )
