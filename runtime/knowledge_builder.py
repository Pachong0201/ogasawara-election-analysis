"""V1.3 knowledge promotion and county-package builder.

This module is deliberately deterministic. It never infers political
relationships from prose. A host agent must submit a structured proposal that
references verified retrieval leads. The builder checks evidence, time
validity, conflicts, idempotence, and provenance before writing knowledge/.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from .election_loader import load_jsonl, safe_component, write_jsonl
from .freshness import historical_relationship_is_current
from .models import parse_date, utc_now_iso


BUILDER_VERSION = "1.3.0"
VALID_GRADES = {"A", "B", "C", "D", "E"}
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}

TARGETS: Dict[str, Dict[str, Any]] = {
    "historical_claim": {
        "id_field": "claim_id",
        "layer_id": "L2",
        "required": ["claim_id", "claim", "time_scope"],
        "path_kind": "historical_claims",
    },
    "local_relationship": {
        "id_field": "relationship_id",
        "layer_id": "L3",
        "required": [
            "relationship_id",
            "subject",
            "object",
            "relationship_type",
            "time_scope",
            "current_status",
        ],
        "path_kind": "relationships",
    },
    "candidate_profile": {
        "id_field": "candidate_id",
        "layer_id": "L3",
        "required": [
            "candidate_id",
            "name",
            "time_scope",
            "last_verified_at",
        ],
        "path_kind": "candidates",
    },
    "current_issue": {
        "id_field": "claim_id",
        "layer_id": "L3",
        "required": [
            "claim_id",
            "claim_text",
            "claim_type",
            "date",
            "time_scope",
            "last_verified_at",
            "verification_status",
        ],
        "path_kind": "issues",
    },
}


class KnowledgePromotionError(ValueError):
    pass


def _load_json_or_jsonl(path: Path) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise KnowledgePromotionError(f"proposal inbox does not exist: {path}")
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("proposals") or payload.get("records") or [payload]
        if not isinstance(payload, list):
            raise KnowledgePromotionError("proposal JSON must contain an object or list")
        return [dict(item) for item in payload if isinstance(item, dict)]

    output: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError as exc:
            raise KnowledgePromotionError(
                f"invalid proposal JSONL at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(item, dict):
            raise KnowledgePromotionError(
                f"proposal JSONL at {path}:{line_number} must contain an object"
            )
        output.append(dict(item))
    return output


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    seen: set[str] = set()
    output: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _record_id(record: Dict[str, Any], target_type: str) -> str:
    spec = TARGETS[target_type]
    return str(record.get(spec["id_field"]) or "").strip()


def _canonical_record(record: Dict[str, Any]) -> str:
    ignored = {
        "promotion_provenance",
        "promoted_at",
        "normalization_version",
    }
    payload = {k: v for k, v in record.items() if k not in ignored}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


class KnowledgePromotionBuilder:
    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]

    def _paths(self, county: str) -> Dict[str, Path]:
        safe = safe_component(county)
        return {
            "historical_claims": self.repo_root / "knowledge" / "historical" / safe / "claims.jsonl",
            "relationships": self.repo_root / "knowledge" / "local" / safe / "relationships.jsonl",
            "candidates": self.repo_root / "knowledge" / "local" / safe / "candidates.jsonl",
            "issues": self.repo_root / "knowledge" / "local" / safe / "issues.jsonl",
            "retrieval": self.repo_root / "cache" / "retrieval" / f"{safe}.jsonl",
            "receipts": self.repo_root / "cache" / "knowledge_promotion" / f"{safe}.jsonl",
            "package_root": self.repo_root / "knowledge" / "counties" / safe,
        }

    def _load_leads(self, county: str) -> List[Dict[str, Any]]:
        path = self._paths(county)["retrieval"]
        return load_jsonl(path) if path.exists() else []

    @staticmethod
    def _lead_map(leads: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        output: Dict[str, Dict[str, Any]] = {}
        for lead in leads:
            lead_id = str(lead.get("lead_id") or lead.get("url") or "").strip()
            if lead_id:
                output[lead_id] = dict(lead)
        return output

    @staticmethod
    def _verified_grade(lead: Dict[str, Any]) -> str:
        if str(lead.get("verification_status") or "").lower() != "verified":
            return ""
        grade = str(lead.get("source_grade") or "").upper()
        return grade if grade in VALID_GRADES else ""

    @staticmethod
    def _evidence_gate(leads: List[Dict[str, Any]]) -> Tuple[bool, List[str], Dict[str, Any]]:
        reasons: List[str] = []
        verified: List[Dict[str, Any]] = []
        for lead in leads:
            grade = KnowledgePromotionBuilder._verified_grade(lead)
            if grade:
                verified.append(lead)

        if not verified:
            reasons.append("no verified evidence lead")
            return False, reasons, {
                "verified": [],
                "grades": [],
                "independent_source_count": 0,
            }

        grades = [str(lead.get("source_grade") or "").upper() for lead in verified]
        strong = [
            lead for lead in verified
            if str(lead.get("source_grade") or "").upper() in {"A", "B"}
        ]
        if strong:
            independence = _dedupe_strings(
                lead.get("independence_key") or lead.get("source_id") or lead.get("url")
                for lead in strong
            )
            return True, reasons, {
                "verified": verified,
                "grades": grades,
                "independent_source_count": max(1, len(independence)),
            }

        verified_c = [
            lead for lead in verified
            if str(lead.get("source_grade") or "").upper() == "C"
        ]
        if len(verified_c) < 2:
            reasons.append("C-grade evidence requires at least two verified independent sources")
            return False, reasons, {
                "verified": verified,
                "grades": grades,
                "independent_source_count": len(verified_c),
            }

        keys = _dedupe_strings(lead.get("independence_key") for lead in verified_c)
        if len(keys) < 2:
            reasons.append(
                "C-grade promotion requires at least two explicit distinct independence_key values"
            )
            return False, reasons, {
                "verified": verified,
                "grades": grades,
                "independent_source_count": len(keys),
            }

        return True, reasons, {
            "verified": verified,
            "grades": grades,
            "independent_source_count": len(keys),
        }

    @staticmethod
    def _best_grade(leads: Iterable[Dict[str, Any]]) -> str:
        grades = [
            str(lead.get("source_grade") or "").upper()
            for lead in leads
            if str(lead.get("source_grade") or "").upper() in VALID_GRADES
        ]
        return min(grades, key=lambda grade: GRADE_ORDER[grade]) if grades else "E"

    @staticmethod
    def _source_descriptor(lead: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "source": str(
                lead.get("source_name")
                or lead.get("source_id")
                or lead.get("title")
                or "retrieval_source"
            ),
            "source_grade": str(lead.get("source_grade") or "E").upper(),
            "reference": str(lead.get("url") or ""),
            "date": str(lead.get("published_at") or "") or None,
            "retrieved_at": str(lead.get("retrieved_at") or ""),
            "lead_id": str(lead.get("lead_id") or lead.get("url") or ""),
            "independence_key": str(lead.get("independence_key") or ""),
        }

    @staticmethod
    def _required_fields(target_type: str, record: Dict[str, Any]) -> List[str]:
        missing = [
            field
            for field in TARGETS[target_type]["required"]
            if record.get(field) in (None, "", [], {})
        ]
        if target_type == "local_relationship":
            status = str(record.get("current_status") or "")
            if status == "active_verified":
                for field in ("last_verified_at",):
                    if record.get(field) in (None, "", [], {}):
                        missing.append(field)
        return sorted(set(missing))

    @staticmethod
    def _time_gate(target_type: str, record: Dict[str, Any]) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        if target_type == "historical_claim":
            if not str(record.get("time_scope") or "").strip():
                reasons.append("historical_claim requires time_scope")
        elif target_type == "local_relationship":
            status = str(record.get("current_status") or "")
            if status not in {"active_verified", "historical_only", "disputed", "unknown"}:
                reasons.append(f"unsupported current_status={status!r}")
            if status == "active_verified":
                probe = dict(record)
                probe.setdefault("source_grade", "B")
                if not historical_relationship_is_current(probe):
                    reasons.append(
                        "active_verified relationship requires a recent last_verified_at within five years"
                    )
        elif target_type in {"candidate_profile", "current_issue"}:
            if parse_date(record.get("last_verified_at")) is None:
                reasons.append(f"{target_type} requires parseable last_verified_at")
        return not reasons, reasons

    @staticmethod
    def _question_gate(
        proposal: Dict[str, Any],
        support_leads: List[Dict[str, Any]],
    ) -> Tuple[bool, List[str], List[str]]:
        proposal_questions = _dedupe_strings(proposal.get("research_questions") or [])
        if not proposal_questions:
            proposal_questions = _dedupe_strings(
                question
                for lead in support_leads
                for question in (
                    lead.get("research_questions")
                    if isinstance(lead.get("research_questions"), list)
                    else [lead.get("query")]
                )
            )
        if not proposal_questions:
            return False, ["proposal must link to at least one research question"], []

        covered: set[str] = set()
        for lead in support_leads:
            values = []
            if lead.get("query"):
                values.append(str(lead.get("query")))
            rq = lead.get("research_questions") or []
            if isinstance(rq, str):
                rq = [rq]
            values.extend(str(item) for item in rq)
            for question in proposal_questions:
                if any(question == value or question in value or value in question for value in values):
                    covered.add(question)

        if not covered:
            return False, ["supporting leads do not cover the proposal research question"], proposal_questions
        return True, [], proposal_questions

    def _evaluate(
        self,
        proposal: Dict[str, Any],
        county_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        reasons: List[str] = []
        warnings: List[str] = []
        proposal_id = str(proposal.get("proposal_id") or "").strip()
        county = str(county_override or proposal.get("county") or "").strip()
        target_type = str(proposal.get("target_type") or "").strip()
        target_record = dict(proposal.get("target_record") or {})

        if not proposal_id:
            reasons.append("missing proposal_id")
        if not county:
            reasons.append("missing county")
        if target_type not in TARGETS:
            reasons.append(f"unsupported target_type={target_type!r}")
            return {
                "passed": False,
                "requires_review": False,
                "proposal_id": proposal_id,
                "county": county,
                "target_type": target_type,
                "record": target_record,
                "support_leads": [],
                "contradictions": [],
                "reasons": reasons,
                "warnings": warnings,
                "research_questions": [],
                "independent_source_count": 0,
                "evidence_grades": [],
            }

        if county_override and proposal.get("county") and str(proposal.get("county")) != county_override:
            reasons.append("proposal county does not match --county")

        missing = self._required_fields(target_type, target_record)
        if missing:
            reasons.append("target_record missing required fields: " + ", ".join(missing))

        lead_map = self._lead_map(self._load_leads(county))
        support_ids = _dedupe_strings(proposal.get("evidence_lead_ids") or [])
        contradiction_ids = _dedupe_strings(proposal.get("contradictory_lead_ids") or [])
        missing_support = [lead_id for lead_id in support_ids if lead_id not in lead_map]
        missing_contradictions = [lead_id for lead_id in contradiction_ids if lead_id not in lead_map]
        if missing_support:
            reasons.append("unresolved evidence_lead_ids: " + ", ".join(missing_support))
        if missing_contradictions:
            warnings.append(
                "unresolved contradictory_lead_ids: " + ", ".join(missing_contradictions)
            )

        support_leads = [lead_map[lead_id] for lead_id in support_ids if lead_id in lead_map]
        contradictions = [
            lead_map[lead_id] for lead_id in contradiction_ids if lead_id in lead_map
        ]

        evidence_ok, evidence_reasons, evidence = self._evidence_gate(support_leads)
        reasons.extend(evidence_reasons)

        time_ok, time_reasons = self._time_gate(target_type, target_record)
        reasons.extend(time_reasons)

        question_ok, question_reasons, research_questions = self._question_gate(
            proposal, support_leads
        )
        reasons.extend(question_reasons)

        verified_contradictions = [
            lead for lead in contradictions
            if self._verified_grade(lead) in {"A", "B", "C"}
        ]
        low_grade_contradictions = [
            lead for lead in contradictions
            if self._verified_grade(lead) in {"D", "E"}
        ]
        if low_grade_contradictions:
            warnings.append("D/E contradictory leads recorded as warnings only")

        requires_review = bool(verified_contradictions)
        if requires_review:
            reasons.append(
                "verified A/B/C contradictory evidence blocks automatic promotion"
            )

        passed = (
            not reasons
            and evidence_ok
            and time_ok
            and question_ok
            and not requires_review
        )

        return {
            "passed": passed,
            "requires_review": requires_review,
            "proposal_id": proposal_id,
            "county": county,
            "target_type": target_type,
            "record": target_record,
            "support_leads": support_leads,
            "contradictions": contradictions,
            "reasons": reasons,
            "warnings": warnings,
            "research_questions": research_questions,
            "independent_source_count": int(evidence.get("independent_source_count") or 0),
            "evidence_grades": list(evidence.get("grades") or []),
        }

    def _normalize_record(self, evaluation: Dict[str, Any]) -> Dict[str, Any]:
        target_type = evaluation["target_type"]
        record = dict(evaluation["record"])
        support = list(evaluation["support_leads"])
        best_grade = self._best_grade(support)
        first = sorted(
            support,
            key=lambda lead: GRADE_ORDER.get(
                str(lead.get("source_grade") or "E").upper(), 99
            ),
        )[0]
        source_descriptors = [self._source_descriptor(lead) for lead in support]
        now = utc_now_iso()

        record["layer_id"] = TARGETS[target_type]["layer_id"]
        record["county"] = evaluation["county"]
        record["source"] = source_descriptors[0]["source"]
        record["source_grade"] = best_grade
        record["source_reference"] = source_descriptors[0]["reference"]
        record["independent_source_count"] = evaluation["independent_source_count"]
        record["research_questions"] = evaluation["research_questions"]
        record["promotion_provenance"] = {
            "proposal_id": evaluation["proposal_id"],
            "promoted_at": now,
            "builder_version": BUILDER_VERSION,
            "evidence_lead_ids": _dedupe_strings(
                lead.get("lead_id") or lead.get("url") for lead in support
            ),
            "evidence_grades": evaluation["evidence_grades"],
        }

        if target_type == "historical_claim":
            record.setdefault("record_type", "historical_claim")
            record["evidence"] = source_descriptors
        elif target_type == "local_relationship":
            record.setdefault("record_type", "local_relationship")
            record["verification_evidence"] = source_descriptors
            if record.get("current_status") != "active_verified":
                record.setdefault("structural_use", "historical_background_only")
            else:
                record.setdefault("structural_use", "can_support_current_interpretation")
        elif target_type == "candidate_profile":
            record.setdefault("record_type", "candidate_profile")
            record["evidence_grade"] = best_grade
            existing_sources = record.get("sources") or []
            record["sources"] = existing_sources or [
                {
                    "source": item["source"],
                    "source_grade": item["source_grade"],
                    "reference": item["reference"],
                    "accessed_at": (item["retrieved_at"] or now)[:10],
                }
                for item in source_descriptors
            ]
        elif target_type == "current_issue":
            record.setdefault("record_type", "political_claim")
            record.setdefault("structural_use", "allowed_with_corroboration")
            record["independent_sources"] = [
                {
                    "source": item["source"],
                    "source_grade": item["source_grade"],
                    "reference": item["reference"],
                }
                for item in source_descriptors[1:]
            ]

        return record

    @staticmethod
    def _newer_or_equal(existing: Dict[str, Any], incoming: Dict[str, Any]) -> bool:
        incoming_date = (
            parse_date(incoming.get("last_verified_at"))
            or parse_date(incoming.get("date"))
            or parse_date(incoming.get("published_at"))
        )
        existing_date = (
            parse_date(existing.get("last_verified_at"))
            or parse_date(existing.get("date"))
            or parse_date(existing.get("published_at"))
        )
        if incoming_date is None or existing_date is None:
            return False
        return incoming_date >= existing_date

    def _persist_record(
        self,
        county: str,
        target_type: str,
        record: Dict[str, Any],
    ) -> Tuple[bool, Path, List[str]]:
        path = self._paths(county)[TARGETS[target_type]["path_kind"]]
        existing = load_jsonl(path) if path.exists() else []
        identity = _record_id(record, target_type)
        warnings: List[str] = []

        replaced = False
        found = False
        output: List[Dict[str, Any]] = []
        for item in existing:
            if _record_id(item, target_type) != identity:
                output.append(item)
                continue
            found = True
            if _canonical_record(item) == _canonical_record(record):
                output.append(item)
                warnings.append("idempotent: identical record already exists")
                replaced = True
                continue
            if self._newer_or_equal(item, record):
                output.append(record)
                warnings.append("existing record replaced by newer/equal verified record")
                replaced = True
                continue
            raise KnowledgePromotionError(
                f"record id conflict for {identity}: incoming record is not demonstrably newer"
            )

        if not found:
            output.append(record)
            replaced = True

        if replaced:
            write_jsonl(path, output)
        return found, path, warnings

    def _write_receipt(self, county: str, receipt: Dict[str, Any]) -> None:
        path = self._paths(county)["receipts"]
        existing = load_jsonl(path) if path.exists() else []
        key = (
            str(receipt.get("proposal_id") or ""),
            str(receipt.get("decision") or ""),
            str(receipt.get("record_id") or ""),
        )
        merged: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for item in existing + [receipt]:
            item_key = (
                str(item.get("proposal_id") or ""),
                str(item.get("decision") or ""),
                str(item.get("record_id") or ""),
            )
            merged[item_key] = item
        write_jsonl(path, merged.values())

    def promote(
        self,
        proposal: Dict[str, Any],
        county_override: Optional[str] = None,
        dry_run: bool = False,
        build_package: bool = True,
    ) -> Dict[str, Any]:
        evaluation = self._evaluate(proposal, county_override=county_override)
        county = evaluation["county"]
        target_type = evaluation["target_type"]
        checked_at = utc_now_iso()

        if evaluation["requires_review"]:
            decision = "requires_review"
        elif not evaluation["passed"]:
            decision = "rejected"
        elif dry_run:
            decision = "dry_run_pass"
        else:
            decision = "promoted"

        target_path = ""
        record_id = ""
        record: Dict[str, Any] = {}
        warnings = list(evaluation["warnings"])

        if evaluation["passed"]:
            record = self._normalize_record(evaluation)
            record_id = _record_id(record, target_type)
            if not dry_run:
                try:
                    _found, path, persist_warnings = self._persist_record(
                        county, target_type, record
                    )
                    target_path = str(path.relative_to(self.repo_root))
                    warnings.extend(persist_warnings)
                except KnowledgePromotionError as exc:
                    decision = "requires_review"
                    evaluation["reasons"].append(str(exc))

        receipt = {
            "proposal_id": evaluation["proposal_id"],
            "county": county,
            "target_type": target_type,
            "decision": decision,
            "checked_at": checked_at,
            "target_path": target_path,
            "record_id": record_id,
            "evidence_lead_ids": _dedupe_strings(
                lead.get("lead_id") or lead.get("url")
                for lead in evaluation["support_leads"]
            ),
            "evidence_grades": evaluation["evidence_grades"],
            "independent_source_count": evaluation["independent_source_count"],
            "research_questions": evaluation["research_questions"],
            "reasons": evaluation["reasons"],
            "warnings": warnings,
            "knowledge_builder_version": BUILDER_VERSION,
        }

        if not dry_run and county:
            self._write_receipt(county, receipt)
            if decision == "promoted" and build_package:
                self.build_county_package(county)

        return {"receipt": receipt, "record": record if decision in {"promoted", "dry_run_pass"} else {}}

    def promote_inbox(
        self,
        proposal_inbox: Path,
        county_override: Optional[str] = None,
        dry_run: bool = False,
        build_package: bool = True,
    ) -> Dict[str, Any]:
        proposals = _load_json_or_jsonl(Path(proposal_inbox))
        results = [
            self.promote(
                proposal,
                county_override=county_override,
                dry_run=dry_run,
                build_package=False,
            )
            for proposal in proposals
        ]

        counties = sorted(
            {
                result["receipt"].get("county")
                for result in results
                if result["receipt"].get("county")
            }
        )
        if not dry_run and build_package:
            for county in counties:
                self.build_county_package(str(county))

        counts: Dict[str, int] = {
            "promoted": 0,
            "rejected": 0,
            "requires_review": 0,
            "dry_run_pass": 0,
        }
        for result in results:
            decision = str(result["receipt"].get("decision") or "")
            if decision in counts:
                counts[decision] += 1
        return {
            "proposal_count": len(proposals),
            "counts": counts,
            "results": results,
            "counties": counties,
        }

    def _all_promoted_evidence_ids(self, county: str) -> set[str]:
        path = self._paths(county)["receipts"]
        receipts = load_jsonl(path) if path.exists() else []
        return {
            str(lead_id)
            for receipt in receipts
            if receipt.get("decision") == "promoted"
            for lead_id in (receipt.get("evidence_lead_ids") or [])
            if str(lead_id).strip()
        }

    def build_county_package(self, county: str) -> Dict[str, Any]:
        paths = self._paths(county)
        package_root = paths["package_root"]
        package_root.mkdir(parents=True, exist_ok=True)

        collections = {
            "historical_claim": load_jsonl(paths["historical_claims"]) if paths["historical_claims"].exists() else [],
            "local_relationship": load_jsonl(paths["relationships"]) if paths["relationships"].exists() else [],
            "candidate_profile": load_jsonl(paths["candidates"]) if paths["candidates"].exists() else [],
            "current_issue": load_jsonl(paths["issues"]) if paths["issues"].exists() else [],
        }

        evidence_index: List[Dict[str, Any]] = []
        for target_type, records in collections.items():
            for record in records:
                evidence_index.append(
                    {
                        "target_type": target_type,
                        "record_id": _record_id(record, target_type),
                        "layer_id": record.get("layer_id") or TARGETS[target_type]["layer_id"],
                        "region": record.get("region") or record.get("jurisdiction") or county,
                        "time_scope": record.get("time_scope", ""),
                        "current_status": record.get("current_status", ""),
                        "source": record.get("source", ""),
                        "source_grade": record.get("source_grade") or record.get("evidence_grade") or "",
                        "independent_source_count": int(record.get("independent_source_count") or 0),
                        "research_questions": record.get("research_questions") or [],
                        "promotion_provenance": record.get("promotion_provenance") or {},
                    }
                )

        evidence_path = package_root / "evidence_index.jsonl"
        write_jsonl(evidence_path, evidence_index)

        promoted_ids = self._all_promoted_evidence_ids(county)
        leads = load_jsonl(paths["retrieval"]) if paths["retrieval"].exists() else []
        unresolved_groups: Dict[str, Dict[str, Any]] = {}
        for lead in leads:
            lead_id = str(lead.get("lead_id") or lead.get("url") or "")
            if lead_id in promoted_ids:
                continue
            question = str(lead.get("query") or "").strip() or "未指定研究问题"
            group = unresolved_groups.setdefault(
                question,
                {
                    "query": question,
                    "status": "unresolved_retrieval_leads",
                    "lead_ids": [],
                    "grades": [],
                    "verified_count": 0,
                },
            )
            group["lead_ids"].append(lead_id)
            grade = str(lead.get("source_grade") or "E").upper()
            group["grades"].append(grade)
            if str(lead.get("verification_status") or "").lower() == "verified":
                group["verified_count"] += 1

        unresolved = []
        for group in unresolved_groups.values():
            group["lead_ids"] = _dedupe_strings(group["lead_ids"])
            group["grades"] = _dedupe_strings(group["grades"])
            unresolved.append(group)
        unresolved_path = package_root / "unresolved_questions.jsonl"
        write_jsonl(unresolved_path, unresolved)

        receipts_path = paths["receipts"]
        receipts = load_jsonl(receipts_path) if receipts_path.exists() else []
        counts = {target_type: len(records) for target_type, records in collections.items()}

        manifest = {
            "version": BUILDER_VERSION,
            "county": county,
            "generated_at": utc_now_iso(),
            "builder": "KnowledgePromotionBuilder",
            "authoritative_sources": {
                target_type: str(
                    paths[TARGETS[target_type]["path_kind"]].relative_to(self.repo_root)
                )
                for target_type in TARGETS
            },
            "generated_indexes": {
                "evidence_index": str(evidence_path.relative_to(self.repo_root)),
                "unresolved_questions": str(unresolved_path.relative_to(self.repo_root)),
                "political_ecology": str((package_root / "political_ecology.md").relative_to(self.repo_root)),
            },
            "counts": counts,
            "evidence_index_count": len(evidence_index),
            "unresolved_question_count": len(unresolved),
            "promotion_receipt_count": len(receipts),
            "rules": {
                "generated_files_are_indexes_not_new_facts": True,
                "source_files_are_authoritative": True,
                "historical_current_separation": True,
            },
        }
        manifest_path = package_root / "package_manifest.yaml"
        manifest_path.write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        historical_lines = [
            f"- {item.get('claim_id')}: {item.get('claim')}（{item.get('time_scope', 'time_scope unknown')}）"
            for item in collections["historical_claim"]
        ]
        relationship_lines = [
            f"- {item.get('relationship_id')}: {item.get('subject')} → {item.get('relationship_type')} → {item.get('object')}；status={item.get('current_status')}；time_scope={item.get('time_scope')}"
            for item in collections["local_relationship"]
        ]
        candidate_lines = [
            f"- {item.get('candidate_id')}: {item.get('name')}；time_scope={item.get('time_scope')}；last_verified_at={item.get('last_verified_at')}"
            for item in collections["candidate_profile"]
        ]
        issue_lines = [
            f"- {item.get('claim_id')}: {item.get('claim_text')}；date={item.get('date')}；time_scope={item.get('time_scope')}"
            for item in collections["current_issue"]
        ]
        unresolved_lines = [
            f"- {item['query']}（leads={len(item['lead_ids'])}, verified={item['verified_count']}）"
            for item in unresolved
        ]

        ecology = "\n".join(
            [
                f"# {county} 地方政治知识索引",
                "",
                "> 本文件由结构化 knowledge/ 记录自动生成，只是索引，不新增因果判断或政治评价。",
                "",
                f"生成时间：{manifest['generated_at']}",
                "",
                "## 历史政治知识（L2）",
                *(historical_lines or ["- 暂无已晋升历史知识。"]),
                "",
                "## 当前地方关系（L3）",
                *(relationship_lines or ["- 暂无已晋升当前地方关系。"]),
                "",
                "## 候选人地方档案（L3）",
                *(candidate_lines or ["- 暂无已晋升候选人档案。"]),
                "",
                "## 当前地方议题（L3）",
                *(issue_lines or ["- 暂无已晋升地方议题。"]),
                "",
                "## 尚未解决的检索问题",
                *(unresolved_lines or ["- 暂无未解决检索问题。"]),
                "",
                "## 使用边界",
                "- 历史记录只在其 time_scope 内有效。",
                "- active_verified 关系必须继续满足 freshness 与当前验证要求。",
                "- retrieval lead 未经晋升不得作为长期结构事实。",
                "- 本索引不得用于候选人评分、排名、胜负预测或政治推荐。",
                "",
            ]
        )
        (package_root / "political_ecology.md").write_text(ecology, encoding="utf-8")

        return {
            "county": county,
            "manifest": manifest,
            "package_root": str(package_root),
        }
