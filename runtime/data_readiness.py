"""Data Readiness Gate for V1.1.

Any full structural analysis must first pass this gate.  The gate is
local-first: it never fetches network data itself.  ``ElectionLoader`` may
fetch and persist data before the gate is re-run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import yaml

from .election_loader import ElectionLoader, election_file_path
from .freshness import evaluate_records, is_fresh
from .knowledge_loader import KnowledgeLoader
from .models import DataQuery, ElectionTask, ReadinessReport, utc_now_iso


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
HARD = "hard"
PREFERRED = "preferred"
CONDITIONAL = "conditional"
OPTIONAL = "optional"


class DataReadinessGate:
    def __init__(self, repo_root: Optional[Path] = None, runtime_config_path: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else DEFAULT_REPO_ROOT
        self.config_path = Path(runtime_config_path) if runtime_config_path else self.repo_root / "config" / "runtime.yaml"
        self.config: Dict[str, Any] = {}
        if self.config_path.exists():
            with self.config_path.open(encoding="utf-8") as fh:
                self.config = yaml.safe_load(fh) or {}
        self.loader = ElectionLoader(self.repo_root, mode="offline")
        self.requirements = self._requirements_for_task_type("county_mayor")

    def _requirements_for_task_type(self, election_type: str) -> Dict[str, Any]:
        all_requirements = self.config.get("analysis_requirements", {})
        return dict(all_requirements.get(election_type, all_requirements.get("county_mayor", {})))

    def expected_years(self, election_type: str, target_year: int, minimum_periods: int = 3) -> List[int]:
        calendar = self.config.get("election_calendar", {})
        years = [int(year) for year in calendar.get(election_type, []) if int(year) < int(target_year)]
        if len(years) < minimum_periods:
            # Fallback for election types not listed in the calendar.
            cycle = 4
            years = [int(target_year) - cycle * step for step in range(1, minimum_periods + 4)]
        return sorted(years)[-minimum_periods:]

    def _load_local_records(self, election_type: str, year: int, jurisdiction: str, level: str) -> List[Dict[str, Any]]:
        query = DataQuery(election_type=election_type, year=int(year), jurisdiction=jurisdiction, level=level)
        result = self.loader._load_local(query)
        return result.records if result.complete else []

    def _has_township_records(self, records: Iterable[Dict[str, Any]]) -> bool:
        return any(record.get("level") == "township_district" for record in records)

    def _period_requirement(
        self,
        election_type: str,
        task: ElectionTask,
        minimum_periods: int,
        requirement_level: str,
        label: str,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[int]]:
        years = self.expected_years(election_type, task.target_year, minimum_periods)
        found_years: List[int] = []
        all_records: List[Dict[str, Any]] = []
        for year in years:
            records = self._load_local_records(election_type, year, task.jurisdiction, task.analysis_level)
            if records:
                found_years.append(year)
                all_records.extend(records)
        satisfied = len(found_years) >= minimum_periods
        info = {
            "label": label,
            "level": requirement_level,
            "minimum_periods": minimum_periods,
            "expected_years": years,
            "found_years": found_years,
            "found_count": len(found_years),
            "satisfied": satisfied,
        }
        return info, all_records, found_years

    def _load_geography(self) -> List[Dict[str, Any]]:
        base = self.repo_root / "data" / "geography" / "administrative_areas"
        roots = [base]
        geography_root = self.repo_root / "data" / "geography"
        if geography_root.exists():
            roots.append(geography_root)
        files = []
        for root in roots:
            if root.exists():
                files.extend(list(root.glob("*.yaml")) + list(root.glob("*.yml")) + list(root.glob("*.json")))
        records: List[Dict[str, Any]] = []
        if not files:
            return records
        for path in sorted(set(files)):
            try:
                if path.suffix == ".json":
                    payload = json.loads(path.read_text(encoding="utf-8"))
                else:
                    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                if "regions" in payload:
                    payload = payload["regions"]
                elif "administrative_areas" in payload:
                    payload = payload["administrative_areas"]
                else:
                    payload = [payload]
            if isinstance(payload, list):
                records.extend(record for record in payload if isinstance(record, dict))
        return records

    def _geography_requirement(self, task: ElectionTask) -> Tuple[Dict[str, Any], bool]:
        regions = self._load_geography()
        region_names = {str(item.get("name") or item.get("region_id") or "") for item in regions}
        region_names.discard("")
        found = task.jurisdiction in region_names
        # A minimal fallback: local election files may include parent jurisdiction,
        # but township-level geography is still required by a hard gate.
        info = {
            "label": "geography",
            "level": HARD,
            "jurisdiction": task.jurisdiction,
            "found": found,
            "region_count": len(regions),
            "satisfied": found,
        }
        return info, found

    def _current_candidates_requirement(self, task: ElectionTask) -> Tuple[Dict[str, Any], List[Dict[str, Any]], bool]:
        cached = self.loader.load_current_candidates(task.jurisdiction)
        warnings: List[str] = []
        candidates: List[Dict[str, Any]] = [dict(item) for item in cached]

        # Names supplied in the user task are only retrieval seeds. They do not
        # satisfy the hard "current candidate list" gate without sourced,
        # time-valid candidate records.
        if not candidates and task.candidates:
            candidates = [
                {
                    "name": name,
                    "candidate_name": name,
                    "candidate_status": "provided_unverified",
                    "source_grade": "",
                }
                for name in task.candidates
            ]
            warnings.append("task-provided candidate names are unverified seeds and do not satisfy the current candidate gate")

        verified: List[Dict[str, Any]] = []
        stale_names: List[str] = []
        rejected_names: List[str] = []
        for candidate in candidates:
            candidate.setdefault("name", candidate.get("candidate_name", ""))
            candidate.setdefault("candidate_status", candidate.get("status", "announced"))
            grade = str(candidate.get("source_grade") or "").upper()
            independent = int(candidate.get("independent_source_count") or 0)
            grade_ok = grade in {"A", "B"} or (grade == "C" and independent >= 2)
            fresh = is_fresh(candidate, kind="candidate_profile")
            status = str(candidate.get("candidate_status") or "").lower()
            status_ok = status in {"registered", "nominated", "announced", "potential"}

            if not fresh:
                stale_names.append(candidate.get("name") or "unknown")
            if not grade_ok or not status_ok:
                rejected_names.append(candidate.get("name") or "unknown")
            if grade_ok and fresh and status_ok:
                verified.append(candidate)

        registered = [
            candidate for candidate in verified
            if str(candidate.get("candidate_status") or "").lower() == "registered"
        ]
        if stale_names:
            warnings.append("stale candidate records require revalidation: " + ", ".join(sorted(set(stale_names))))
        if rejected_names:
            warnings.append("candidate records failed source/status validation: " + ", ".join(sorted(set(rejected_names))))

        satisfied = bool(verified)
        info = {
            "label": "current_candidates",
            "level": HARD,
            "count": len(candidates),
            "verified_count": len(verified),
            "registered_count": len(registered),
            "candidates": candidates,
            "verified_candidates": verified,
            "warnings": warnings,
            "satisfied": satisfied,
        }
        return info, verified, satisfied

    def _candidate_profiles_requirement(self, current_candidates: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], bool]:
        has_profile = any(candidate.get("birth_place") or candidate.get("past_constituencies") or candidate.get("offices") for candidate in current_candidates)
        info = {
            "label": "candidate_profiles",
            "level": CONDITIONAL,
            "count": len(current_candidates),
            "satisfied": has_profile,
        }
        return info, has_profile

    def _polls_requirement(self, task: ElectionTask) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        loaded = self.loader.load_polls(jurisdiction=task.jurisdiction)
        records = loaded.get("records", [])
        invalid = loaded.get("invalid", [])
        freshness = evaluate_records(records, kind="poll") if records else {"fresh": 0, "stale": 0, "unknown": 0, "records": []}
        info = {
            "label": "current_polls",
            "level": OPTIONAL,
            "count": len(records),
            "invalid_count": len(invalid),
            "fresh_count": freshness.get("fresh", 0),
            "stale_count": freshness.get("stale", 0),
            "satisfied": len(records) > 0,
            "freshness": freshness,
            "invalid": invalid,
        }
        return info, freshness

    def _local_knowledge_requirement(self, task: ElectionTask) -> Dict[str, Any]:
        loader = KnowledgeLoader(self.repo_root, mode="offline")
        knowledge = loader.load_local_knowledge(task.jurisdiction)
        return {
            "label": "local_knowledge",
            "level": CONDITIONAL,
            "count": len(knowledge.get("historical_claims", [])) + len(knowledge.get("relationships", [])) + len(knowledge.get("candidates", [])) + len(knowledge.get("issues", [])),
            "satisfied": bool(knowledge.get("sufficient")),
            "warnings": knowledge.get("warnings", []),
        }

    def _source_grade_check(self, records: Iterable[Dict[str, Any]]) -> Tuple[bool, List[str], List[str]]:
        allowed = {str(grade).upper() for grade in self.config.get("readiness", {}).get("allowed_fact_source_grades", ["A", "B"])}
        warning_grades = {str(grade).upper() for grade in self.config.get("readiness", {}).get("warning_fact_source_grades", ["C"])}
        forbidden = {str(grade).upper() for grade in self.config.get("readiness", {}).get("forbidden_fact_source_grades", ["D", "E"])}
        errors: List[str] = []
        warnings: List[str] = []
        for record in records:
            grade = str(record.get("source_grade") or "").upper()
            if grade in forbidden:
                errors.append(f"forbidden source_grade={grade!r} for a historical fact record")
            elif grade in warning_grades:
                warnings.append(f"source_grade={grade} is below preferred A/B for a historical fact record")
        return not errors, errors, warnings

    def boundary_compatible(self, records: Iterable[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        versions = {str(record.get("boundary_version") or "") for record in records}
        versions.discard("")
        if len(versions) <= 1:
            return True, sorted(versions)
        mapping_dir = self.repo_root / "data" / "geography" / "boundary_versions"
        mapping_files = list(mapping_dir.glob("*.yaml")) + list(mapping_dir.glob("*.yml")) + list(mapping_dir.glob("*.json")) if mapping_dir.exists() else []
        if not mapping_files:
            return False, sorted(versions)
        # A mapping file must explicitly list the observed versions as compatible.
        for path in mapping_files:
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8")) if path.suffix != ".json" else json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            compatible = set()
            if isinstance(payload, dict):
                compatible = set(str(item) for item in (payload.get("compatible_versions") or []))
                mappings = payload.get("mappings") or []
                for mapping in mappings if isinstance(mappings, list) else []:
                    if isinstance(mapping, dict):
                        compatible.update(str(item) for item in (mapping.get("versions") or []))
            if versions.issubset(compatible):
                return True, sorted(versions)
        return False, sorted(versions)

    def check(self, task: ElectionTask) -> ReadinessReport:
        self.requirements = self._requirements_for_task_type(task.election_type)
        required: Dict[str, Any] = {}
        available: Dict[str, Any] = {}
        missing: List[str] = []
        stale: List[str] = []
        warnings: List[str] = []

        # Historical same-type requirement.
        same_req = self.requirements.get("historical_same_type", {"minimum_periods": 3, "required": True})
        same_info, same_records, same_years = self._period_requirement(
            task.election_type,
            task,
            int(same_req.get("minimum_periods", 3)),
            HARD if same_req.get("required") is True else PREFERRED,
            "historical_same_type",
        )
        required["historical_same_type"] = same_info
        available["historical_same_type"] = {"found_years": same_years, "record_count": len(same_records)}
        if not same_info["satisfied"]:
            missing.append(f"historical_same_type: need {same_info['minimum_periods']} periods, found {same_info['found_count']}")

        # President requirement (for county mayoral analyses; for presidential races this is the same type and is deduplicated below).
        president_req = self.requirements.get("president")
        president_records: List[Dict[str, Any]] = []
        if president_req and task.election_type != "president":
            pres_info, president_records, pres_years = self._period_requirement(
                "president",
                task,
                int(president_req.get("minimum_periods", 3)),
                HARD if president_req.get("required") is True else PREFERRED,
                "presidential",
            )
            required["presidential"] = pres_info
            available["presidential"] = {"found_years": pres_years, "record_count": len(president_records)}
            if not pres_info["satisfied"]:
                missing.append(f"presidential: need {pres_info['minimum_periods']} periods, found {pres_info['found_count']}")
        else:
            required["presidential"] = {"level": HARD if task.election_type == "president" else OPTIONAL, "applicable": False, "satisfied": True}
            available["presidential"] = {"applicable": False}

        # Regional legislator preferred requirement.
        legislator_req = self.requirements.get("regional_legislator")
        legislator_records: List[Dict[str, Any]] = []
        if legislator_req and task.election_type != "regional_legislator":
            leg_info, legislator_records, leg_years = self._period_requirement(
                "regional_legislator",
                task,
                int(legislator_req.get("minimum_periods", 3)),
                PREFERRED,
                "regional_legislator",
            )
            required["regional_legislator"] = leg_info
            available["regional_legislator"] = {"found_years": leg_years, "record_count": len(legislator_records)}
            if not leg_info["satisfied"]:
                warnings.append(f"regional_legislator: preferred data missing ({leg_info['found_count']}/{leg_info['minimum_periods']} periods)")
        else:
            required["regional_legislator"] = {"level": PREFERRED if task.election_type != "regional_legislator" else HARD, "applicable": False, "satisfied": True}
            available["regional_legislator"] = {"applicable": False}

        # Geography requirement.
        geo_info, geo_found = self._geography_requirement(task)
        required["geography"] = geo_info
        available["geography"] = {"found": geo_found}
        if not geo_found:
            missing.append(f"geography: no administrative area record for {task.jurisdiction}")

        # Township-level requirement: scan all historical records loaded above.
        all_historical = same_records + president_records + legislator_records
        township_found = self._has_township_records(all_historical)
        required["township_level"] = {
            "label": "township_level",
            "level": HARD,
            "required": True,
            "found": township_found,
            "satisfied": township_found,
        }
        available["township_level"] = {"found": township_found}
        if not township_found:
            missing.append("township_level: no township_district records found")

        # Boundary compatibility.
        compatible, versions = self.boundary_compatible(all_historical)
        required["boundary_compatibility"] = {
            "label": "boundary_compatibility",
            "level": HARD,
            "boundary_versions": versions,
            "satisfied": compatible,
        }
        available["boundary_compatibility"] = {"versions": versions, "compatible": compatible}
        if not compatible:
            missing.append(f"boundary_compatibility: incompatible boundary_versions {versions} without mapping")

        # Source grade check.
        grade_ok, grade_errors, grade_warnings = self._source_grade_check(all_historical)
        warnings.extend(grade_warnings)
        required["source_grade"] = {
            "label": "source_grade",
            "level": HARD,
            "satisfied": grade_ok,
            "errors": grade_errors,
        }
        available["source_grade"] = {"passed": grade_ok}
        if not grade_ok:
            missing.extend(grade_errors)

        # Current candidates.
        candidate_info, current_candidates, candidates_found = self._current_candidates_requirement(task)
        required["current_candidate_list"] = candidate_info
        available["current_candidates"] = candidate_info
        warnings.extend(candidate_info.get("warnings", []))
        if not candidates_found:
            missing.append("current_candidate_list: no fresh, adequately sourced current candidates could be confirmed")

        # Candidate profiles.
        profile_info, profiles_found = self._candidate_profiles_requirement(current_candidates)
        required["candidate_profiles"] = profile_info
        available["candidate_profiles"] = {"count": len(current_candidates), "satisfied": profiles_found}

        # Local knowledge is conditional on anomaly discovery; readiness records the cache state but does not hard-fail.
        local_info = self._local_knowledge_requirement(task)
        required["local_knowledge"] = local_info
        available["local_knowledge"] = local_info
        if not local_info["satisfied"]:
            warnings.append("local_knowledge: no cached local knowledge; residual explanations will require retrieval or remain unknown")

        # Polls are optional for readiness; stale polls are reported but do not block.
        poll_info, poll_freshness = self._polls_requirement(task)
        required["current_polls"] = poll_info
        available["current_polls"] = poll_info
        for item in poll_freshness.get("records", []):
            if item.get("status") == "stale":
                stale.append(f"poll stale: {item.get('record_id') or item.get('index')}")

        # Determine status.
        hard_satisfied = all(
            item.get("satisfied", True)
            for key, item in required.items()
            if str(item.get("level")) == HARD and item.get("applicable", True) is not False
        )
        preferred_satisfied = all(
            item.get("satisfied", True)
            for key, item in required.items()
            if str(item.get("level")) == PREFERRED and item.get("applicable", True) is not False
        )
        if not hard_satisfied:
            status = "INSUFFICIENT"
        elif not preferred_satisfied:
            status = "PARTIAL"
        else:
            status = "READY"

        if status == "PARTIAL":
            warnings.append("readiness=PARTIAL: conclusions must be reduced to the available evidence scope")
        if status == "INSUFFICIENT":
            warnings.append("readiness=INSUFFICIENT: full structural analysis is not allowed")

        return ReadinessReport(
            task=task,
            status=status,
            required=required,
            available=available,
            missing=sorted(set(missing)),
            stale=sorted(set(stale)),
            warnings=warnings,
            mode="offline",
            generated_at=utc_now_iso(),
        )


    def prepare(
        self,
        task: ElectionTask,
        loader: Optional[ElectionLoader] = None,
        allow_online: bool = True,
    ) -> ReadinessReport:
        """Run readiness, attempt to fill missing historical periods, then re-check.

        This is the programmatic bridge between the local-first gate and the
        injected adapter layer.  It does not invent data: if no adapter can
        supply a missing period, the refreshed report remains INSUFFICIENT.
        """
        initial = self.check(task)
        if initial.status == "READY" or not allow_online:
            return initial

        loader = loader or ElectionLoader(self.repo_root, mode="online")
        attempts: List[str] = []
        # Historical same type.
        for year in initial.required.get("historical_same_type", {}).get("expected_years", []):
            if year not in initial.required["historical_same_type"].get("found_years", []):
                attempts.append(f"{task.election_type}:{year}")
                loader.load_election(task.election_type, int(year), task.jurisdiction, task.analysis_level)
        # President, when applicable.
        pres = initial.required.get("presidential", {})
        if pres.get("applicable", True) is not False:
            for year in pres.get("expected_years", []):
                if year not in pres.get("found_years", []):
                    attempts.append(f"president:{year}")
                    loader.load_election("president", int(year), task.jurisdiction, task.analysis_level)
        # Regional legislator, when applicable.
        leg = initial.required.get("regional_legislator", {})
        if leg.get("applicable", True) is not False:
            for year in leg.get("expected_years", []):
                if year not in leg.get("found_years", []):
                    attempts.append(f"regional_legislator:{year}")
                    loader.load_election("regional_legislator", int(year), task.jurisdiction, task.analysis_level)

        # Current candidates are a hard, time-sensitive requirement. Task-provided
        # names are retrieval seeds only; refresh from a registered official source
        # when the verified/fresh cache is absent.
        candidate_req = initial.required.get("current_candidate_list", {})
        if not candidate_req.get("satisfied", False):
            attempts.append(f"current_candidates:{task.target_year}")
            loader.refresh_current_candidates(
                task.jurisdiction,
                task.election_type,
                int(task.target_year),
            )

        # Polls are optional. Fill an empty cache when a primary poll source is
        # registered, but never make readiness depend on a poll being available.
        poll_req = initial.required.get("current_polls", {})
        if int(poll_req.get("count") or 0) == 0:
            attempts.append(f"current_polls:{task.target_year}")
            loader.refresh_polls(
                task.jurisdiction,
                task.election_type,
                int(task.target_year),
            )

        refreshed = self.check(task)
        if attempts:
            refreshed.warnings.append("data preparation attempted fills: " + ", ".join(attempts))
        return refreshed


def report_status_upper(report: ReadinessReport) -> str:
    return str(report.status).upper()
