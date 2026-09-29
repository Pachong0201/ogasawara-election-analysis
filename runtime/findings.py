"""Turn raw metrics into ranked, citable findings for the report writer.

A finding is a single, source-traceable statement with its magnitude,
comparability caveats, competing explanations and a concrete research
question. Findings describe vote patterns; they never assign causes. Causal
language belongs to local knowledge, and only when that knowledge is verified.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .aggregates import MAJOR_PARTIES, ElectionAggregate, display_key, is_independent

TYPE_LABELS = {
    "county_mayor": "县市长",
    "president": "总统",
    "regional_legislator": "区域立委",
    "township_mayor": "乡镇市长",
    "councilor": "县市议员",
}

COMPETING_EXPLANATIONS = {
    "local_swing": [
        "候选人更替或候选人在当地的个人经营差异",
        "地方组织动员力量的变化",
        "投票率变化带来的投票人群构成变化",
    ],
    "candidate_residual": [
        "候选人在当地的地缘关系或长期经营",
        "对手阵营在当地的组织强弱",
        "作为基准的总统选举本身在当地的特殊性",
    ],
    "split_ticket": [
        "立委候选人的个人因素",
        "地方议员、乡镇市长组织与立委选举的配合程度",
        "政党在不同层级选举中的动员差异",
    ],
    "turnout_change": [
        "动员强弱差异",
        "人口迁移或设籍变化",
        "同日其他选举的带动效果",
    ],
}
SMALL_SAMPLE_EXPLANATION = "有效票基数小，百分点差异对少数选票变化非常敏感；若为山地原住民乡，另有独立的选民结构"


def pp(value: Optional[float]) -> str:
    return "unknown" if value is None else f"{value * 100:.1f}"


def pct(value: Optional[float]) -> str:
    return "unknown" if value is None else f"{value * 100:.1f}%"


def signed_pp(value: float) -> str:
    return f"{'+' if value >= 0 else '-'}{abs(value) * 100:.1f}"


def source_id(election_type: str, year: int) -> str:
    return f"S-cec-{election_type}-{year}"


def _best_grade(grades: Iterable[str]) -> str:
    ordered = sorted(g for g in grades if g)
    return ordered[0] if ordered else "unknown"


class FindingsBuilder:
    """Build the findings layer from records, metrics and the campaign state."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        report_cfg = self.config.get("findings", {}) if isinstance(self.config, dict) else {}
        self.max_township_findings = int(report_cfg.get("max_township_findings_per_metric", 6))
        self.max_storylines = int(report_cfg.get("max_storylines", 5))
        self.stable_range_pp = float(report_cfg.get("stable_range_pp", 4.0))
        self._township_builders = {
            "local_swing": self._township_local_swing,
            "candidate_residual": self._township_candidate_residual,
            "split_ticket": self._township_split_ticket,
            "turnout_change": self._township_turnout_change,
        }

    def build(
        self,
        task: Any,
        records_by_type: Dict[str, List[Dict[str, Any]]],
        metrics: Dict[str, List[Dict[str, Any]]],
        campaign_state: Optional[Dict[str, Any]] = None,
        current_candidates: Optional[List[Dict[str, Any]]] = None,
        polls: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        self._findings: List[Dict[str, Any]] = []
        self._sources: Dict[str, Dict[str, Any]] = {}
        self.task = task
        aggregates = {
            etype: ElectionAggregate(records, independents="candidate")
            for etype, records in records_by_type.items()
            if records
        }
        for etype, agg in aggregates.items():
            for year in agg.years():
                self._sources[source_id(etype, year)] = {
                    "id": source_id(etype, year),
                    "label": f"中选会 {year} 年{TYPE_LABELS.get(etype, etype)}选举官方开放资料",
                    "source_grade": _best_grade(agg.source_grades.get(year, set())),
                    "source_ids": sorted(agg.source_ids.get(year, set())),
                }

        same = aggregates.get(task.election_type)
        overview = self._county_overview(task.election_type, same) if same else []
        baseline_range = self._baseline_range(aggregates)
        typology = self._township_typology(aggregates, metrics)
        third_force = self._third_force(aggregates)
        self._county_swings(task.election_type, metrics.get("local_swing", []))
        self._county_candidate_residuals(metrics.get("candidate_residuals", []))
        self._county_split_ticket(metrics.get("split_ticket", []))
        self._spatial(task.election_type, metrics.get("spatial_anomalies", []))
        self._township_metric("local_swing", metrics.get("local_swing", []))
        self._township_metric("candidate_residual", metrics.get("candidate_residuals", []))
        self._township_metric("split_ticket", metrics.get("split_ticket", []))
        self._township_metric("turnout_change", metrics.get("turnout_change", []))
        self._concentration(metrics.get("geographic_concentration", []))
        campaign = self._campaign(
            task,
            aggregates,
            campaign_state or {},
            current_candidates or [],
            polls or [],
        )

        self._assign_ids()
        storylines = self._storylines()
        research_questions = [
            f["research_question"] for f in self._findings if f.get("research_question")
        ]
        return {
            "county_overview": overview,
            "baseline_range": baseline_range,
            "township_typology": typology,
            "third_force": third_force,
            "campaign": campaign,
            "findings": self._findings,
            "storylines": storylines,
            "research_questions": list(dict.fromkeys(research_questions)),
            "sources": sorted(self._sources.values(), key=lambda s: s["id"]),
            "interpretation_boundary": (
                "findings state vote-pattern facts and open questions; they do not attribute causes, "
                "rank candidates or predict outcomes"
            ),
        }

    # ------------------------------------------------------------------ helpers

    def _add(self, **finding: Any) -> Dict[str, Any]:
        finding.setdefault("claim_type", "fact")
        finding.setdefault("status", "confirmed")
        finding.setdefault("evidence_grade", "A")
        finding.setdefault("comparability", {})
        finding.setdefault("competing_explanations", [])
        finding.setdefault("research_question", None)
        finding.setdefault("score", 0.0)
        finding.setdefault("numbers", [])
        self._findings.append(finding)
        return finding

    def _assign_ids(self) -> None:
        category_order = {"county": 0, "campaign": 1, "township": 2}
        self._findings.sort(key=lambda f: (category_order.get(f["level"], 3), -float(f.get("score") or 0.0)))
        for index, finding in enumerate(self._findings, start=1):
            finding["finding_id"] = f"F-{index:03d}"
            finding["magnitude_rank"] = index

    # ------------------------------------------------------------------ county

    def _county_overview(self, etype: str, agg: ElectionAggregate) -> List[Dict[str, Any]]:
        overview = []
        for year in agg.years():
            ranked = agg.ranked_candidates(year)
            if not ranked:
                continue
            turnout = agg.county_turnout(year)
            winner = ranked[0]
            runner = ranked[1] if len(ranked) > 1 else None
            margin = (winner["vote_share"] or 0.0) - ((runner or {}).get("vote_share") or 0.0)
            row = {
                "election_type": etype,
                "year": year,
                "candidates": [
                    {"candidate": r["candidate"], "party": r["party"], "votes": r["votes"], "vote_share": round(r["vote_share"] or 0.0, 6)}
                    for r in ranked
                ],
                "turnout": None if turnout is None else round(turnout, 6),
                "margin": round(margin, 6),
                "source": source_id(etype, year),
            }
            overview.append(row)
            statement = f"{year}年{TYPE_LABELS.get(etype, etype)}选举，{winner['candidate']}（{winner['party']}）得票率{pct(winner['vote_share'])}"
            if runner:
                statement += f"，{runner['candidate']}（{runner['party']}）{pct(runner['vote_share'])}，差距{pp(margin)}个百分点"
            if turnout is not None:
                statement += f"；估算投票率约{pct(turnout)}"
            self._add(
                level="county",
                kind="election_result",
                statement=statement + "。",
                numbers=[winner["vote_share"], (runner or {}).get("vote_share"), margin, turnout],
                sources=[source_id(etype, year)],
                year=year,
                score=100.0 + year / 10000.0,
            )
        return overview

    def _baseline_range(self, aggregates: Dict[str, ElectionAggregate]) -> Dict[str, Any]:
        ranges: Dict[str, Any] = {}
        for etype, agg in aggregates.items():
            per_party: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
            for year in agg.years():
                for key, share in agg.ranked_keys(year):
                    if key in MAJOR_PARTIES:
                        per_party[key].append((year, share))
            ranges[etype] = {}
            for party, series in per_party.items():
                values = [v for _, v in series]
                ranges[etype][party] = {
                    "series": [{"year": y, "vote_share": round(v, 6)} for y, v in series],
                    "min": round(min(values), 6),
                    "median": round(statistics.median(values), 6),
                    "max": round(max(values), 6),
                }
                if len(series) >= 2:
                    text = "、".join(f"{y}年{pct(v)}" for y, v in series)
                    self._add(
                        level="county",
                        kind="baseline_range",
                        statement=f"{party}在{TYPE_LABELS.get(etype, etype)}选举的全县得票率：{text}；区间{pct(min(values))}–{pct(max(values))}。",
                        numbers=values,
                        sources=[source_id(etype, y) for y, _ in series],
                        election_type=etype,
                        party=party,
                        score=50.0 + (max(values) - min(values)) * 100,
                    )
        return ranges

    def _county_swings(self, etype: str, rows: List[Dict[str, Any]]) -> None:
        seen = set()
        for row in rows:
            key = (row.get("series_key"), tuple(row.get("periods") or []))
            if key in seen or not row.get("periods"):
                continue
            seen.add(key)
            if is_independent(row.get("party")):
                continue
            y0, y1 = row["periods"]
            swing = float(row.get("county_swing") or 0.0)
            comp = row.get("comparability", {})
            note = ""
            if comp.get("candidate_changed"):
                note = f"（候选人由{'、'.join(comp.get('previous_candidates') or ['无'])}换为{'、'.join(comp.get('current_candidates') or ['无'])}）"
            self._add(
                level="county",
                kind="county_swing",
                statement=f"{y0}→{y1}年{TYPE_LABELS.get(etype, etype)}选举，{row['party']}全县得票率变化{signed_pp(swing)}个百分点{note}。",
                numbers=[swing],
                sources=[source_id(etype, y0), source_id(etype, y1)],
                comparability=comp,
                party=row["party"],
                periods=[y0, y1],
                score=abs(swing) * 100,
            )

    def _county_candidate_residuals(self, rows: List[Dict[str, Any]]) -> None:
        seen = set()
        for row in rows:
            key = (row.get("party"), row.get("election_year"))
            if key in seen:
                continue
            seen.add(key)
            county = float(row.get("county_residual") or 0.0)
            left, right = row.get("president_years") or [None, None]
            direction = "高" if county >= 0 else "低"
            self._add(
                level="county",
                kind="cross_level_gap",
                statement=(
                    f"{row['election_year']}年{row.get('candidate')}（{row['party']}）的县长全县得票率，"
                    f"比该党{left}、{right}年两次总统选举在{self.task.jurisdiction}的平均得票{direction}{pp(abs(county))}个百分点。"
                ),
                numbers=[county],
                sources=[source_id("county_mayor", row["election_year"]), source_id("president", left), source_id("president", right)],
                party=row["party"],
                year=row["election_year"],
                claim_type="fact",
                note="这是候选人得票与同党总统票基准的统计差，不等于“个人票”",
                score=abs(county) * 100 + 5,
            )

    def _county_split_ticket(self, rows: List[Dict[str, Any]]) -> None:
        seen = set()
        for row in rows:
            key = (row.get("party"), row.get("year"))
            if key in seen:
                continue
            seen.add(key)
            county = float(row.get("county_residual") or 0.0)
            if abs(county) < 0.03:
                continue
            direction = "高" if county >= 0 else "低"
            cands = "、".join(row.get("legislator_candidates") or [])
            self._add(
                level="county",
                kind="county_split_ticket",
                statement=f"{row['year']}年同日选举中，{row['party']}区域立委（{cands}）得票率比同党总统候选人{direction}{pp(abs(county))}个百分点。",
                numbers=[county],
                sources=[source_id("regional_legislator", row["year"]), source_id("president", row["year"])],
                party=row["party"],
                year=row["year"],
                score=abs(county) * 100,
            )

    def _spatial(self, etype: str, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            if row.get("threshold_status") not in {"observe", "strong_trigger"}:
                continue
            high = row.get("highest_areas") or []
            low = row.get("lowest_areas") or []
            hi_text = "、".join(f"{a['region']}{pct(a['vote_share'])}" for a in high[:2])
            lo_text = "、".join(f"{a['region']}{pct(a['vote_share'])}" for a in low[:2])
            self._add(
                level="county",
                kind="spatial_dispersion",
                statement=(
                    f"{row['election_year']}年{display_key(row['series_key'])}在各乡镇市的得票率加权标准差为{pp(row.get('observed_value'))}个百分点，"
                    f"最高为{hi_text}，最低为{lo_text}。"
                ),
                numbers=[row.get("observed_value")] + [a["vote_share"] for a in high[:2] + low[:2]],
                sources=[source_id(etype, row["election_year"])],
                party=row.get("party"),
                note="高离散只说明地理分布不均，不能直接解释为派系",
                status="needs_local_knowledge",
                research_question=(
                    f"{row['election_year']}年{display_key(row['series_key'])}在{high[0]['region'] if high else '得票最高乡镇'}与"
                    f"{low[0]['region'] if low else '得票最低乡镇'}之间的得票落差，来自选民结构、候选人地缘还是地方组织差异？"
                ),
                score=float(row.get("observed_value") or 0.0) * 100,
            )

    def _concentration(self, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            if not row.get("local_explanation_required"):
                continue
            details = row.get("details", {})
            self._add(
                level="county",
                kind="geographic_concentration",
                statement=(
                    f"{row['election_year']}年{display_key(row['series_key'])}得票最多的{'、'.join(details.get('top_areas', []))}"
                    f"贡献其全县票数的{pct(details.get('candidate_vote_share_top'))}，而这些地区只占选举人约{pct(details.get('electorate_share_top'))}。"
                ),
                numbers=[details.get("candidate_vote_share_top"), details.get("electorate_share_top")],
                sources=[source_id(self.task.election_type, row["election_year"])],
                score=float(row.get("residual") or 0.0) * 100,
            )

    # ---------------------------------------------------------------- township

    def _township_metric(self, kind: str, rows: List[Dict[str, Any]]) -> None:
        triggered = [r for r in rows if r.get("local_explanation_required")]
        for row in triggered:
            row["_score"] = abs(float(row.get("residual") or 0.0)) * 100 * (0.5 if row.get("small_sample") else 1.0)
        triggered.sort(key=lambda r: r["_score"], reverse=True)
        for row in triggered[: self.max_township_findings]:
            score = row.pop("_score")
            payload = self._township_builders[kind](row)
            explanations = list(COMPETING_EXPLANATIONS.get(kind, []))
            if row.get("small_sample"):
                explanations.append(SMALL_SAMPLE_EXPLANATION)
            self._add(
                level="township",
                kind=kind,
                region=row.get("region"),
                status="needs_local_knowledge",
                threshold_status=row.get("threshold_status"),
                small_sample=bool(row.get("small_sample")),
                valid_votes=row.get("valid_votes"),
                competing_explanations=explanations,
                score=score,
                **payload,
            )
        for row in rows:
            row.pop("_score", None)

    def _township_local_swing(self, row: Dict[str, Any]) -> Dict[str, Any]:
        y0, y1 = row["periods"]
        rel = float(row["residual"])
        name = display_key(row["series_key"])
        comp = row.get("comparability", {})
        direction = "多" if rel >= 0 else "少"
        statement = (
            f"{y0}→{y1}年，{name}在{row['region']}的得票率由{pct(row['previous_vote_share'])}变为{pct(row['vote_share'])}"
            f"（{signed_pp(row['raw_swing'])}个百分点），比全县平均摆动（{signed_pp(row['county_swing'])}）{direction}变动{pp(abs(rel))}个百分点。"
        )
        who = "、".join(comp.get("current_candidates") or []) or name
        question = (
            f"{y1}年{who}（{row['party']}）在{row['region']}的得票变化为何比全县平均{'更有利' if rel >= 0 else '更不利'}约{pp(abs(rel))}个百分点？"
            f"是否与候选人在当地的经营、地方公职人员动员或对手组织变化有关？"
        )
        return {
            "statement": statement,
            "numbers": [row["previous_vote_share"], row["vote_share"], row["raw_swing"], row["county_swing"], rel],
            "sources": [source_id(self.task.election_type, y0), source_id(self.task.election_type, y1)],
            "party": row["party"],
            "periods": [y0, y1],
            "comparability": comp,
            "research_question": question,
            "metrics": [f"local_swing:{row['region']}:{row['series_key']}:{y0}->{y1}"],
        }

    def _township_candidate_residual(self, row: Dict[str, Any]) -> Dict[str, Any]:
        rel = float(row["relative_residual"])
        left, right = row["president_years"]
        year = row["election_year"]
        statement = (
            f"{year}年{row['candidate']}（{row['party']}）在{row['region']}的得票率{pct(row['observed_value'])}，"
            f"比该党{left}/{right}年总统票基准（{pct(row['baseline_value'])}）{'高' if row['absolute_residual'] >= 0 else '低'}{pp(abs(row['absolute_residual']))}个百分点；"
            f"扣除全县共同落差（{signed_pp(row['county_residual'])}）后，仍{'高' if rel >= 0 else '低'}于全县{pp(abs(rel))}个百分点。"
        )
        question = (
            f"{row['candidate']}在{row['region']}的{year}年得票为何相对同党总统票基准{'特别强' if rel >= 0 else '特别弱'}"
            f"（较全县相对差{signed_pp(rel)}个百分点）？当地是否有候选人的地缘、长期服务或组织网络？"
        )
        return {
            "statement": statement,
            "numbers": [row["observed_value"], row["baseline_value"], row["absolute_residual"], row["county_residual"], rel],
            "sources": [source_id("county_mayor", year), source_id("president", left), source_id("president", right)],
            "party": row["party"],
            "year": year,
            "research_question": question,
            "metrics": [f"candidate_residual:{row['region']}:{row['party']}:{year}"],
            "note": "统计残差不等于个人票",
        }

    def _township_split_ticket(self, row: Dict[str, Any]) -> Dict[str, Any]:
        rel = float(row["relative_residual"])
        year = row["year"]
        cands = "、".join(row.get("legislator_candidates") or []) or row["party"]
        statement = (
            f"{year}年{row['region']}的{row['party']}立委得票（{cands}）比同党总统票{'高' if row['raw_residual'] >= 0 else '低'}{pp(abs(row['raw_residual']))}个百分点，"
            f"与全县同一落差（{signed_pp(row['county_residual'])}）相比偏离{signed_pp(rel)}个百分点。"
        )
        question = (
            f"{year}年{row['region']}选民为何在{row['party']}的总统票与立委票（{cands}）之间出现{pp(abs(row['raw_residual']))}个百分点的分裂？"
            f"当地乡镇长、议员与立委候选人的组织关系如何？"
        )
        return {
            "statement": statement,
            "numbers": [row["raw_residual"], row["county_residual"], rel],
            "sources": [source_id("regional_legislator", year), source_id("president", year)],
            "party": row["party"],
            "year": year,
            "research_question": question,
            "metrics": [f"split_ticket:{row['region']}:{row['party']}:{year}"],
        }

    def _township_turnout_change(self, row: Dict[str, Any]) -> Dict[str, Any]:
        y0, y1 = row["periods"]
        rel = float(row["residual"])
        statement = (
            f"{y0}→{y1}年{row['region']}的投票率由{pct(row['previous_turnout'])}变为{pct(row['turnout'])}，"
            f"比全县投票率变化偏离{signed_pp(rel)}个百分点。"
        )
        question = f"{y1}年{row['region']}投票率相对全县{'上升' if rel >= 0 else '下降'}{pp(abs(rel))}个百分点，是哪一方的动员变化所致，还是人口或设籍变化？"
        return {
            "statement": statement,
            "numbers": [row["previous_turnout"], row["turnout"], rel],
            "sources": [source_id(self.task.election_type, y0), source_id(self.task.election_type, y1)],
            "periods": [y0, y1],
            "research_question": question,
            "metrics": [f"turnout_change:{row['region']}:{y0}->{y1}"],
        }

    # ---------------------------------------------------------------- typology

    def _township_typology(self, aggregates: Dict[str, ElectionAggregate], metrics: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        pres = aggregates.get("president")
        if not pres:
            return []
        years = pres.years()
        rel_residual: Dict[str, List[float]] = defaultdict(list)
        for row in metrics.get("candidate_residuals", []):
            rel_residual[str(row.get("region"))].append(abs(float(row.get("relative_residual") or 0.0)))
        split: Dict[str, List[float]] = defaultdict(list)
        split_not_comparable: set = set()
        for row in metrics.get("split_ticket", []):
            if row.get("electorate_mismatch"):
                split_not_comparable.add(str(row.get("region")))
                continue
            split[str(row.get("region"))].append(abs(float(row.get("relative_residual") or 0.0)))
        latest = years[-1]
        county_dpp = {y: pres.county_share(y, MAJOR_PARTIES[0]) for y in years}
        rows = []
        for region in pres.regions(latest):
            dpp = [pres.share(y, region, MAJOR_PARTIES[0]) for y in years]
            gaps = [v - county_dpp[y] for y, v in zip(years, dpp) if v is not None and county_dpp.get(y) is not None]
            if len(gaps) < 2:
                continue
            spread = (max(gaps) - min(gaps)) * 100
            lean = pres.share(latest, region, MAJOR_PARTIES[0]) or 0.0
            lean_kmt = pres.share(latest, region, MAJOR_PARTIES[1]) or 0.0
            cand = statistics.fmean(rel_residual[region]) * 100 if rel_residual.get(region) else 0.0
            spl = statistics.fmean(split[region]) * 100 if split.get(region) else 0.0
            tags = []
            tags.append("稳定区" if spread < self.stable_range_pp else "摇摆区")
            if cand >= 5.0:
                tags.append("候选人敏感区")
            if spl >= 5.0:
                tags.append("跨层级分裂区")
            rows.append({
                "region": region,
                "types": tags,
                "president_dpp_shares": {str(y): None if v is None else round(v, 6) for y, v in zip(years, dpp)},
                "president_dpp_gap_to_county_pp": [round(g * 100, 2) for g in gaps],
                "president_dpp_gap_range_pp": round(spread, 2),
                "latest_president_lean": "民主進步黨" if lean >= lean_kmt else "中國國民黨",
                "mean_abs_relative_candidate_residual_pp": round(cand, 2),
                "mean_abs_relative_split_ticket_pp": round(spl, 2),
                "valid_votes_latest_president": pres.valid(latest, region),
                "split_ticket_comparable": region not in split_not_comparable,
                "sources": [source_id("president", y) for y in years],
            })
        return rows

    # ---------------------------------------------------------------- third force

    def _third_force(self, aggregates: Dict[str, ElectionAggregate]) -> Dict[str, Any]:
        result: Dict[str, Any] = {"by_election": [], "known": [], "unknown": []}
        for etype, agg in aggregates.items():
            for year in agg.years():
                others = [(k, s) for k, s in agg.ranked_keys(year) if k not in MAJOR_PARTIES and s >= 0.03]
                if not others:
                    continue
                total = sum(s for _, s in others)
                top_key = others[0][0]
                shares = sorted(
                    ((r, agg.share(year, r, top_key) or 0.0) for r in agg.regions(year)),
                    key=lambda item: item[1],
                    reverse=True,
                )
                entry = {
                    "election_type": etype,
                    "year": year,
                    "lists": [{"name": display_key(k), "vote_share": round(s, 6)} for k, s in others],
                    "total_vote_share": round(total, 6),
                    "strongest_areas": [{"region": r, "vote_share": round(v, 6)} for r, v in shares[:3]],
                    "source": source_id(etype, year),
                }
                result["by_election"].append(entry)
                lists_text = "、".join(f"{display_key(k)}{pct(s)}" for k, s in others)
                area_text = "、".join(f"{r}{pct(v)}" for r, v in shares[:2])
                self._add(
                    level="county",
                    kind="third_force",
                    statement=f"{year}年{TYPE_LABELS.get(etype, etype)}选举中，两大党以外得票达3%以上的有：{lists_text}；{display_key(top_key)}得票最高的是{area_text}。",
                    numbers=[s for _, s in others] + [v for _, v in shares[:2]],
                    sources=[source_id(etype, year)],
                    year=year,
                    note="第三势力票不得与任一大党票机械相加",
                    score=total * 100,
                )
        result["known"] = ["第三势力在各届选举中的得票规模", "第三势力得票的空间分布"]
        result["unknown"] = [
            "第三势力选民的来源（原本投给哪一方）",
            "第三势力选民在本届的转移方向",
            "第三势力是否因候选人或议题而流动",
            "第三势力选民的投票率变化",
            "弃保效应的实际规模",
        ]
        return result

    # ---------------------------------------------------------------- campaign

    def _campaign(
        self,
        task: Any,
        aggregates: Dict[str, ElectionAggregate],
        campaign_state: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        polls: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        as_of = str(campaign_state.get("as_of") or "")[:10] or "unknown"
        cand_rows = []
        for cand in candidates:
            name = str(cand.get("candidate_name") or cand.get("name") or "")
            party = str(cand.get("recommended_by_party") or cand.get("party") or "")
            history = []
            for etype, agg in aggregates.items():
                for year in agg.years():
                    for row in agg.ranked_candidates(year):
                        if row["candidate"] == name:
                            history.append({"election_type": etype, "year": year, "vote_share": round(row["vote_share"] or 0.0, 6), "source": source_id(etype, year)})
            cand_rows.append({
                "candidate": name,
                "party": party or "未由政黨推薦",
                "status": cand.get("candidate_status"),
                "source_grade": cand.get("source_grade") or cand.get("evidence_grade"),
                "source_reference": cand.get("source_reference") or cand.get("source"),
                "history_in_loaded_elections": history,
            })
            for item in history:
                self._add(
                    level="campaign",
                    kind="candidate_history",
                    statement=f"本届登记参选人{name}曾参加{item['year']}年{TYPE_LABELS.get(item['election_type'], item['election_type'])}选举，全县得票率{pct(item['vote_share'])}。",
                    numbers=[item["vote_share"]],
                    sources=[item["source"]],
                    score=item["vote_share"] * 100,
                )
        if cand_rows:
            self._sources["S-cec-candidates-current"] = {
                "id": "S-cec-candidates-current",
                "label": "中选会本届候选人登记公告",
                "source_grade": _best_grade(str(c.get("source_grade") or "") for c in cand_rows),
                "source_ids": ["cec_current_candidates"],
            }
            names = "、".join(f"{c['candidate']}（{c['party']}）" for c in cand_rows)
            self._add(
                level="campaign",
                kind="current_candidates",
                statement=f"截至{as_of}，本届{TYPE_LABELS.get(task.election_type, task.election_type)}登记参选人共{len(cand_rows)}位：{names}。",
                sources=["S-cec-candidates-current"],
                evidence_grade=_best_grade(str(c.get("source_grade") or "") for c in cand_rows),
                score=90.0,
            )

        fresh, stale = [], []
        for poll in polls:
            sid = f"S-poll-{poll.get('poll_id')}"
            self._sources[sid] = {
                "id": sid,
                "label": f"{poll.get('pollster')}《{poll.get('report_title') or ''}》（{poll.get('field_start')}至{poll.get('field_end')}）",
                "source_grade": poll.get("source_grade"),
                "source_ids": [poll.get("source_id")],
            }
            row = {
                "source": sid,
                "pollster": poll.get("pollster"),
                "field_end": poll.get("field_end"),
                "sample_size": poll.get("sample_size"),
                "moe": poll.get("moe"),
                "undecided": poll.get("undecided"),
                "question_wording": poll.get("question_wording"),
                "candidate_support": poll.get("candidate_support"),
                "freshness_status": poll.get("freshness_status"),
                "source_grade": poll.get("source_grade"),
            }
            (fresh if poll.get("freshness_status") == "fresh" else stale).append(row)
            support = "、".join(f"{c.get('candidate')}{pct(c.get('support'))}" for c in poll.get("candidate_support") or [])
            self._add(
                level="campaign",
                kind="poll_fresh" if row in fresh else "poll_stale",
                statement=(
                    f"{poll.get('pollster')}于{poll.get('field_start')}至{poll.get('field_end')}调查（样本{poll.get('sample_size')}，误差±{poll.get('moe')}个百分点）：{support}，"
                    f"未表态{pct(poll.get('undecided'))}。"
                    + ("" if row in fresh else "该民调已过期，只能作为当时的时点证据，不能校准当前选情。")
                ),
                numbers=[c.get("support") for c in poll.get("candidate_support") or []] + [poll.get("undecided")],
                sources=[sid],
                evidence_grade=poll.get("source_grade") or "unknown",
                claim_type="fact",
                status="confirmed" if row in fresh else "stale",
                score=40.0 if row in fresh else 10.0,
            )

        registered = {c["candidate"] for c in cand_rows}
        if registered:
            for poll in polls:
                named = [str(c.get("candidate") or "") for c in poll.get("candidate_support") or []]
                absent = [n for n in named if n and n not in registered]
                if absent:
                    self._add(
                        level="campaign",
                        kind="poll_field_mismatch",
                        statement=(
                            f"{poll.get('pollster')}（{poll.get('field_end')}）调查的候选人组合包含{'、'.join(absent)}，"
                            f"但其不在截至{as_of}的登记名单中，该民调情境与当前参选格局不可直接比较。"
                        ),
                        sources=[f"S-poll-{poll.get('poll_id')}", "S-cec-candidates-current"],
                        claim_type="fact",
                        score=60.0,
                    )

        windows = campaign_state.get("windows", {})
        return {
            "as_of": as_of,
            "candidates": cand_rows,
            "event_counts": {k: v.get("event_count", 0) for k, v in windows.items()},
            "verified_trigger_event_counts": {k: v.get("verified_trigger_event_count", 0) for k, v in windows.items()},
            "campaign_change_trigger": bool(campaign_state.get("campaign_change_trigger")),
            "campaign_change_reasons": campaign_state.get("campaign_change_reasons", []),
            "snapshot_delta": campaign_state.get("snapshot_delta", {}),
            "same_series_poll_changes": campaign_state.get("same_series_poll_changes", []),
            "fresh_polls": fresh,
            "stale_polls": stale,
        }

    # ---------------------------------------------------------------- storylines

    def _storylines(self) -> List[Dict[str, Any]]:
        by_kind: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for f in self._findings:
            by_kind[f["kind"]].append(f)
        for rows in by_kind.values():
            rows.sort(key=lambda f: -float(f.get("score") or 0.0))

        def ids(kinds: Iterable[str], limit: int) -> List[str]:
            chosen: List[Dict[str, Any]] = []
            for kind in kinds:
                chosen.extend(by_kind.get(kind, []))
            chosen.sort(key=lambda f: -float(f.get("score") or 0.0))
            return [f["finding_id"] for f in chosen[:limit]]

        candidates = [
            ("当前选战格局与历史对照", ["current_candidates", "candidate_history", "poll_fresh", "poll_stale", "poll_field_mismatch", "election_result"], 6),
            ("县长票与总统票的结构落差", ["cross_level_gap", "county_split_ticket", "county_swing"], 4),
            ("变化最集中的乡镇", ["local_swing", "candidate_residual"], 4),
            ("跨层级分票与地方组织", ["split_ticket"], 3),
            ("第三势力与地理分布", ["third_force", "spatial_dispersion", "geographic_concentration"], 3),
            ("投票率与动员差异", ["turnout_change"], 3),
        ]
        storylines = []
        for title, kinds, limit in candidates:
            chosen = ids(kinds, limit)
            if chosen:
                storylines.append({"theme": title, "finding_ids": chosen})
        return storylines[: self.max_storylines]
