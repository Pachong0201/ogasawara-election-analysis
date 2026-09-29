"""Report brief, prompt assembly and write → validate → rewrite loop.

The LLM never sees the raw 100k-character context. It receives a compact
brief built from the findings layer, plus the writing prompt and rules.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

import yaml

from .models import AnalysisContext, as_jsonable
from .report_validator import ReportValidator, ValidationResult

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LANGUAGE_INSTRUCTIONS = {
    "zh-CN": "全文使用简体中文。人名、地名、政党名保留资料中的原始写法（如宜蘭縣、民主進步黨），其余文字不得混用繁体。",
    "zh-TW": "全文使用繁體中文（臺灣用語）。數字與人名、地名、政黨名保持資料原樣。",
}


class LLMBackend(Protocol):
    def generate(self, system: str, user: str) -> str:
        ...


class OpenAICompatibleBackend:
    """Minimal chat-completions client (OpenAI-compatible APIs) using the standard library."""

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.4,
        timeout: int = 300,
    ):
        self.model = model or os.environ.get("REPORT_LLM_MODEL", "gpt-4o")
        self.base_url = (base_url or os.environ.get("REPORT_LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("REPORT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        self.temperature = temperature
        self.timeout = timeout

    def generate(self, system: str, user: str) -> str:
        if not self.api_key:
            raise RuntimeError("REPORT_LLM_API_KEY / OPENAI_API_KEY is not set")
        body = json.dumps({
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return str(payload["choices"][0]["message"]["content"])


def _trim(items: List[Any], limit: int) -> List[Any]:
    return list(items[:limit])


def build_report_brief(context: AnalysisContext) -> Dict[str, Any]:
    """Compact, citation-ready payload for the report writer."""
    ctx = as_jsonable(context.analysis_context)
    findings = ctx.get("findings") or {}
    campaign = findings.get("campaign", {})
    local = ctx.get("local_knowledge") or {}
    knowledge_records = []
    for bucket in ("relationships", "candidates", "issues", "historical_claims"):
        for record in local.get(bucket, []) or []:
            knowledge_records.append({
                "layer": bucket,
                "claim": record.get("claim") or record.get("statement") or record.get("summary"),
                "claim_type": record.get("claim_type"),
                "source_grade": record.get("source_grade") or record.get("evidence_grade"),
                "time_scope": record.get("time_scope"),
                "regions": record.get("regions") or record.get("region"),
            })
    return {
        "task": ctx.get("task", {}),
        "as_of": campaign.get("as_of") or str((ctx.get("campaign_state") or {}).get("as_of") or "")[:10] or "unknown",
        "readiness_status": (ctx.get("readiness") or {}).get("status"),
        "missing_data": (ctx.get("readiness") or {}).get("missing", []),
        "county_overview": findings.get("county_overview", []),
        "baseline_range": findings.get("baseline_range", {}),
        "storylines": findings.get("storylines", []),
        "findings": [
            {k: v for k, v in f.items() if k not in {"score"}} for f in findings.get("findings", [])
        ],
        "township_typology": findings.get("township_typology", []),
        "third_force": findings.get("third_force", {}),
        "campaign": campaign,
        "local_knowledge": {
            "sufficient": bool(local.get("sufficient")),
            "missing": local.get("missing", []),
            "records": _trim(knowledge_records, 20),
        },
        "unknowns": ctx.get("unknowns", []),
        "warnings": _trim(ctx.get("warnings", []), 15),
        "sources": findings.get("sources", []),
        "interpretation_boundary": findings.get("interpretation_boundary"),
    }


@dataclass
class ReportResult:
    report: str
    validation: ValidationResult
    rounds: int
    history: List[Dict[str, Any]] = field(default_factory=list)


class ReportWriter:
    def __init__(
        self,
        backend: Optional[LLMBackend] = None,
        repo_root: Optional[Path] = None,
        validator: Optional[ReportValidator] = None,
        max_rounds: Optional[int] = None,
    ):
        root = Path(repo_root) if repo_root else PACKAGE_ROOT
        self.backend = backend
        self.prompt_path = self._resolve(root, "prompts/report_writer.md")
        self.rules_path = self._resolve(root, "rules/writing_rules.yaml")
        self.example_dir = self._resolve(root, "examples/reports")
        self.validator = validator or ReportValidator(self.rules_path)
        with self.rules_path.open(encoding="utf-8") as fh:
            self.rules = yaml.safe_load(fh) or {}
        self.max_rounds = int(max_rounds or self.rules.get("report_contract", {}).get("max_rewrite_rounds", 3))

    @staticmethod
    def _resolve(root: Path, rel: str) -> Path:
        path = root / rel
        return path if path.exists() else PACKAGE_ROOT / rel

    def _exemplars(self) -> str:
        if not self.example_dir.exists():
            return ""
        texts = [p.read_text(encoding="utf-8") for p in sorted(self.example_dir.glob("exemplar_*.md"))]
        return "\n\n---\n\n".join(texts)

    def build_messages(self, brief: Dict[str, Any], language: str = "zh-CN") -> Dict[str, str]:
        system = self.prompt_path.read_text(encoding="utf-8")
        sections = [
            {"title": s.get("title"), **{k: v for k, v in s.items() if k not in {"key", "title"}}}
            for s in self.rules.get("default_output_sections", [])
        ]
        user = "\n\n".join([
            f"## 语言\n{LANGUAGE_INSTRUCTIONS.get(language, LANGUAGE_INSTRUCTIONS['zh-CN'])}",
            "## 章节结构（writing_rules.yaml）\n" + yaml.safe_dump(sections, allow_unicode=True, sort_keys=False),
            "## 写作范例（只学写法，不得引用其中的数字或事实）\n" + (self._exemplars() or "（无）"),
            "## 报告 brief（唯一可用的事实来源）\n```json\n" + json.dumps(brief, ensure_ascii=False, separators=(",", ":")) + "\n```",
            f"请写出完整报告。as_of = {brief.get('as_of')}。",
        ])
        return {"system": system, "user": user}

    def write(self, brief: Dict[str, Any], language: str = "zh-CN") -> ReportResult:
        if self.backend is None:
            raise RuntimeError("no LLM backend configured; use build_messages() and let the host agent write")
        messages = self.build_messages(brief, language)
        history: List[Dict[str, Any]] = []
        report = self.backend.generate(messages["system"], messages["user"])
        validation = self.validator.validate(report, brief)
        history.append({"round": 1, "validation": validation.to_dict()})
        rounds = 1
        while not validation.passed and rounds < self.max_rounds:
            rounds += 1
            revise = (
                messages["user"]
                + "\n\n## 上一稿\n"
                + report
                + "\n\n## 校验器发现的问题（必须全部修正，其余内容尽量保留）\n"
                + validation.feedback()
                + "\n\n请输出修改后的完整报告。"
            )
            report = self.backend.generate(messages["system"], revise)
            validation = self.validator.validate(report, brief)
            history.append({"round": rounds, "validation": validation.to_dict()})
        return ReportResult(report=report, validation=validation, rounds=rounds, history=history)
