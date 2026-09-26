"""Resolve live retrieval leads into conservative, traceable campaign events.

This layer sits between public article retrieval and CampaignStateBuilder:

article body -> evidence extraction -> entity resolution -> cross-source
corroboration -> campaign event.

Important evidence boundary:
- a single media report remains a single-source report;
- two or more independent publisher bodies may become corroborated_media;
- corroboration is allowed to trigger further research and snapshot deltas;
- it does NOT upgrade the underlying claims into an A/B verified political fact.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

from .models import parse_date


EVENT_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "candidate_withdrawal": (
        "退選", "退选", "退出選舉", "退出选举", "宣布退出", "不參選", "不参选",
    ),
    "candidate_registration": (
        "登記參選", "登记参选", "完成登記", "完成登记", "候選人登記", "候选人登记",
    ),
    "nomination": (
        "提名", "徵召", "征召", "獲提名", "获提名", "正式提名",
    ),
    "alliance_break": (
        "決裂", "决裂", "拆夥", "拆伙", "退出聯盟", "退出联盟", "合作破局",
    ),
    "party_cooperation": (
        "政黨合作", "政党合作", "共同支持", "共同推薦", "共同推荐", "整合",
        "藍白合", "蓝白合", "跨黨", "跨党", "結盟", "结盟",
    ),
    "campaign_headquarters": (
        "競選總部", "竞选总部", "競總", "竞总", "總部成立", "总部成立",
        "競選辦公室", "竞选办公室",
    ),
    "endorsement": (
        "力挺", "站台", "背書", "背书", "表態支持", "表态支持", "公開支持", "公开支持",
        "相挺", "支持參選", "支持参选",
    ),
    "campaign_org": (
        "後援會", "后援会", "造勢", "造势", "動員", "动员", "里長", "里长",
        "組織", "组织", "輔選", "辅选", "成立後援", "成立后援",
    ),
    "debate": (
        "辯論", "辩论", "政見會", "政见会", "公開辯論", "公开辩论", "debate",
    ),
    "judicial_event": (
        "起訴", "起诉", "判決", "判决", "搜索", "約談", "约谈", "檢調", "检调",
        "法院", "檢察官", "检察官", "羈押", "羁押",
    ),
    "controversy": (
        "爭議", "争议", "風波", "风波", "爆料", "質疑", "质疑", "道歉",
        "抨擊", "抨击", "指控", "爭論", "争论",
    ),
    "policy": (
        "政見", "政见", "政策", "提出方案", "政策主張", "政策主张", "政策牛肉",
        "承諾", "承诺", "主張", "主张",
    ),
    "poll": (
        "民調", "民调", "支持度", "調查", "调查", "poll", "survey",
    ),
    "major_issue": (
        "治安", "房價", "房价", "交通", "空污", "環境", "环境", "產業", "产业",
        "就業", "就业", "社福", "醫療", "医疗", "漁港", "渔港", "建設", "建设",
    ),
}

EVENT_PRIORITY = (
    "candidate_withdrawal",
    "candidate_registration",
    "nomination",
    "alliance_break",
    "party_cooperation",
    "campaign_headquarters",
    "endorsement",
    "campaign_org",
    "debate",
    "judicial_event",
    "controversy",
    "policy",
    "poll",
    "major_issue",
)

SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
LOCATION_RE = re.compile(r"([\u3400-\u9fff]{1,6}(?:市|縣|县|區|区|鄉|乡|鎮|镇|村|里))")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
ALNUM_RE = re.compile(r"[A-Za-z0-9]{2,}")


def _norm_text(value: Any) -> str:
    text = str(value or "").strip()
    return (
        text.replace("臺", "台")
        .replace("蘭", "兰").replace("義", "义").replace("東", "东")
        .replace("雲", "云").replace("蓮", "莲").replace("門", "门").replace("連", "连")
        .replace("縣", "县")
        .replace("區", "区")
        .replace("鄉", "乡")
        .replace("鎮", "镇")
        .replace("選", "选")
        .replace("舉", "举")
        .replace("競", "竞")
        .replace("總", "总")
        .replace("會", "会")
        .replace("組", "组")
        .replace("織", "织")
        .replace("黨", "党")
        .replace("聯", "联")
        .replace("後", "后")
        .replace("調", "调")
        .replace("議", "议")
        .replace("爭", "争")
    )


def _record_date(record: Dict[str, Any]) -> Tuple[Optional[dt.date], str]:
    for key, basis in (
        ("event_date", "event_date"),
        ("date", "date"),
        ("page_date", "publisher_page_date"),
        ("publish_date", "publish_date"),
        ("published_at", "published_at"),
        ("first_seen_at", "first_seen_at"),
        ("retrieved_at", "retrieved_at"),
    ):
        if record.get("source_kind") and key in ("first_seen_at", "retrieved_at"):
            continue
        parsed = parse_date(record.get(key))
        if parsed:
            return parsed, basis
    return None, ""


def _candidate_names(current_candidates: Iterable[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for row in current_candidates or []:
        name = str(
            row.get("candidate_name")
            or row.get("name")
            or row.get("姓名")
            or ""
        ).strip()
        if name and name not in names:
            names.append(name)
    return names


def _resolve_candidates(text: str, names: Sequence[str]) -> List[str]:
    normalized = _norm_text(text)
    resolved: List[str] = []
    for name in names:
        if _norm_text(name) in normalized and name not in resolved:
            resolved.append(name)
    return resolved


def _resolve_locations(text: str, jurisdiction: str) -> List[str]:
    normalized_jurisdiction = _norm_text(jurisdiction)
    out: List[str] = []
    for match in LOCATION_RE.findall(text):
        value = str(match).strip()
        if not value:
            continue
        if _norm_text(value) == normalized_jurisdiction:
            continue
        if value not in out:
            out.append(value)
        if len(out) >= 6:
            break
    return out


def _keyword_counts(text: str) -> Dict[str, int]:
    normalized = _norm_text(text).lower()
    counts: Dict[str, int] = {}
    for event_type, keywords in EVENT_KEYWORDS.items():
        count = 0
        for keyword in keywords:
            token = _norm_text(keyword).lower()
            if token:
                count += normalized.count(token)
        if count:
            counts[event_type] = count
    return counts


def _event_type(text: str, query: str = "") -> Tuple[str, Dict[str, int]]:
    counts = _keyword_counts(text)
    query_counts = _keyword_counts(query)
    for key, value in query_counts.items():
        counts[key] = counts.get(key, 0) + min(value, 1)
    if not counts:
        return "campaign_update", {}
    order = {name: index for index, name in enumerate(EVENT_PRIORITY)}
    selected = sorted(
        counts.items(),
        key=lambda item: (-item[1], order.get(item[0], 999), item[0]),
    )[0][0]
    return selected, counts


def _sentence_score(sentence: str, candidate_names: Sequence[str], event_type: str) -> int:
    score = 0
    normalized = _norm_text(sentence)
    for name in candidate_names:
        if _norm_text(name) in normalized:
            score += 4
    for keyword in EVENT_KEYWORDS.get(event_type, ()):
        if _norm_text(keyword) in normalized:
            score += 2
    if 25 <= len(sentence) <= 240:
        score += 1
    return score


def _evidence_excerpt(
    title: str,
    content: str,
    candidate_names: Sequence[str],
    event_type: str,
    max_chars: int = 900,
) -> str:
    sentences = [
        value.strip()
        for value in SENTENCE_SPLIT.split(content)
        if value and value.strip()
    ]
    ranked = sorted(
        enumerate(sentences),
        key=lambda pair: (-_sentence_score(pair[1], candidate_names, event_type), pair[0]),
    )
    chosen: List[Tuple[int, str]] = []
    for index, sentence in ranked:
        if _sentence_score(sentence, candidate_names, event_type) <= 0:
            continue
        chosen.append((index, sentence))
        if len(chosen) >= 3:
            break
    if not chosen and sentences:
        chosen = [(0, sentences[0])]
    chosen.sort(key=lambda pair: pair[0])
    excerpt = " ".join(sentence for _, sentence in chosen).strip()
    if not excerpt:
        excerpt = title.strip()
    return excerpt[:max_chars]


def _fingerprint_tokens(text: str) -> Set[str]:
    normalized = re.sub(r"\s+", "", _norm_text(text).lower())
    tokens: Set[str] = set(ALNUM_RE.findall(normalized))
    han = "".join(ch for ch in normalized if CJK_RE.match(ch))
    for size in (2, 3):
        for index in range(max(0, len(han) - size + 1)):
            token = han[index:index + size]
            if len(token) == size:
                tokens.add(token)
    return tokens


def _jaccard(left: Set[str], right: Set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


@dataclass
class _EvidenceItem:
    lead: Dict[str, Any]
    date: dt.date
    date_basis: str
    event_type: str
    candidates: List[str]
    locations: List[str]
    excerpt: str
    tokens: Set[str]
    domain: str


def _should_cluster(left: _EvidenceItem, right: _EvidenceItem) -> bool:
    if left.event_type != right.event_type:
        return False
    if abs((left.date - right.date).days) > 1:
        return False

    left_candidates = set(left.candidates)
    right_candidates = set(right.candidates)
    candidate_overlap = bool(left_candidates & right_candidates)
    location_overlap = bool(set(left.locations) & set(right.locations))
    similarity = _jaccard(left.tokens, right.tokens)

    if candidate_overlap:
        return similarity >= 0.12 or location_overlap
    if location_overlap:
        return similarity >= 0.24
    return similarity >= 0.42


def _cluster(items: Sequence[_EvidenceItem]) -> List[List[_EvidenceItem]]:
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        lroot, rroot = find(left), find(right)
        if lroot != rroot:
            parent[rroot] = lroot

    for left in range(len(items)):
        for right in range(left + 1, len(items)):
            if _should_cluster(items[left], items[right]):
                union(left, right)

    grouped: Dict[int, List[_EvidenceItem]] = {}
    for index, item in enumerate(items):
        grouped.setdefault(find(index), []).append(item)
    return list(grouped.values())


def _event_id(
    jurisdiction: str,
    event_type: str,
    date: dt.date,
    candidates: Sequence[str],
    locations: Sequence[str],
    excerpts: Sequence[str],
) -> str:
    token_sets = [_fingerprint_tokens(text) for text in excerpts if text]
    common = set.intersection(*token_sets) if len(token_sets) >= 2 else (token_sets[0] if token_sets else set())
    fingerprint = sorted(token for token in common if len(token) >= 2)[:12]
    seed = {
        "jurisdiction": _norm_text(jurisdiction),
        "event_type": event_type,
        "date": date.isoformat(),
        "candidates": sorted(_norm_text(value) for value in candidates),
        "locations": sorted(_norm_text(value) for value in locations)[:3],
        "fingerprint": fingerprint,
    }
    digest = hashlib.sha256(
        json.dumps(seed, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:18]
    return f"campaign-event-{digest}"


def _independent_publishers(members):
    """Union matching publishers, wire attribution and near-identical bodies."""
    parent = list(range(len(members)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    keys, bodies = [], []
    for item in members:
        content = str(item.lead.get("content") or "")
        key = item.lead.get("original_publisher_id") or item.lead.get("publisher_id") or item.domain
        # Syndicated CNA credit; an ordinary mention of CNA does not suffice.
        if re.search(r"(?:中央社記者|中央社记者|中央社[）)]|來源[：:]\s*中央社|来源[：:]\s*中央社)", content):
            key = "cna"
        if "cna.com.tw" in str(key):
            key = "cna"
        keys.append(str(key))
        bodies.append(_fingerprint_tokens(content))
    for i in range(len(members)):
        for j in range(i):
            if keys[i] == keys[j] or _jaccard(bodies[i], bodies[j]) >= 0.85:
                parent[root(i)] = root(j)
    return sorted({keys[root(i)] for i in range(len(members)) if keys[root(i)]})


class CampaignEventResolver:
    """Create conservative campaign events from article bodies."""

    def __init__(
        self,
        corroboration_min_sources: int = 2,
        max_excerpt_chars: int = 900,
    ):
        self.corroboration_min_sources = max(2, int(corroboration_min_sources))
        self.max_excerpt_chars = max(200, min(int(max_excerpt_chars), 1800))

    def extract(
        self,
        leads: Iterable[Dict[str, Any]],
        jurisdiction: str,
        current_candidates: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        names = _candidate_names(current_candidates or [])
        evidence_items: List[_EvidenceItem] = []
        ignored: Dict[str, int] = {}

        for lead in leads or []:
            if not isinstance(lead, dict):
                continue
            kind = lead.get("source_kind")
            if kind and (kind != "media" or lead.get("source_grade") != "C"):
                ignored["non_media_lead"] = ignored.get("non_media_lead", 0) + 1
                continue
            status = str(lead.get("body_status") or "")
            content = str(lead.get("content") or "").strip()
            if status != "read" or not content:
                key = status or "no_body"
                ignored[key] = ignored.get(key, 0) + 1
                continue

            date, date_basis = _record_date(lead)
            if not date:
                ignored["no_date"] = ignored.get("no_date", 0) + 1
                continue

            title = str(lead.get("page_title") or lead.get("title") or "").strip()
            query = str(lead.get("query") or "")
            combined = f"{title}\n{content}"
            event_type, _ = _event_type(combined, query="" if kind else query)
            candidates = _resolve_candidates(combined, names)
            locations = _resolve_locations(combined, jurisdiction)
            excerpt = _evidence_excerpt(
                title,
                content,
                candidates,
                event_type,
                max_chars=self.max_excerpt_chars,
            )
            domain = str(
                lead.get("independence_key")
                or lead.get("source_name")
                or urlparse(str(lead.get("url") or "")).hostname
                or ""
            ).lower().strip()
            evidence_items.append(
                _EvidenceItem(
                    lead=dict(lead),
                    date=date,
                    date_basis=date_basis,
                    event_type=event_type,
                    candidates=candidates,
                    locations=locations,
                    excerpt=excerpt,
                    tokens=_fingerprint_tokens(f"{title} {excerpt}"),
                    domain=domain,
                )
            )

        events: List[Dict[str, Any]] = []
        for members in _cluster(evidence_items):
            members = sorted(
                members,
                key=lambda item: (
                    item.date.isoformat(),
                    str(item.lead.get("url") or ""),
                ),
            )
            event_type = members[0].event_type
            dates = sorted(item.date for item in members)
            event_date = dates[-1]
            candidates = sorted({name for item in members for name in item.candidates})
            locations = sorted({name for item in members for name in item.locations})
            domains = _independent_publishers(members)
            requires_review = any(
                item.lead.get("requires_review") or re.search(r"否認|否认|澄清|撤稿|更正", item.excerpt)
                for item in members
            )
            excerpts = [item.excerpt for item in members if item.excerpt]
            corroborated = len(domains) >= self.corroboration_min_sources and not requires_review

            sources = []
            for item in members:
                lead = item.lead
                sources.append(
                    {
                        "title": str(lead.get("title") or ""),
                        "url": str(lead.get("url") or ""),
                        "domain": item.domain,
                        "source_kind": lead.get("source_kind", "media"),
                        "source_grade": lead.get("source_grade", "E"),
                        "article_version": lead.get("article_version"),
                        "date_basis": item.date_basis,
                        "page_date": str(lead.get("page_date") or ""),
                        "first_seen_at": str(lead.get("first_seen_at") or ""),
                        "body_status": str(lead.get("body_status") or ""),
                        "content_sha256": str(lead.get("content_sha256") or ""),
                        "evidence_excerpt": item.excerpt,
                    }
                )

            event_id = _event_id(
                jurisdiction,
                event_type,
                event_date,
                candidates,
                locations,
                excerpts,
            )
            events.append(
                {
                    "event_id": event_id,
                    "record_type": "campaign_event",
                    "jurisdiction": jurisdiction,
                    "date": event_date.isoformat(),
                    "event_date": event_date.isoformat(),
                    "date_basis": "media_report_date_proxy",
                    "event_type": event_type,
                    "claim_type": event_type,
                    "candidate_entities": candidates,
                    "locations": locations,
                    "independent_source_count": len(domains),
                    "independence_keys": domains,
                    "verification_status": (
                        "requires_review" if requires_review else ("corroborated_media" if corroborated else "single_source_media")
                    ),
                    "source_grade": "C",
                    "source_id": "campaign_event_resolver",
                    "evidence_status": (
                        "conflict_or_correction" if requires_review else ("cross_source_corroborated" if corroborated else "single_source_report")
                    ),
                    "structural_use": (
                        "research_trigger_only" if corroborated else "context_only"
                    ),
                    "source_urls": [item["url"] for item in sources if item["url"]],
                    "sources": sources,
                    "evidence_excerpt": " | ".join(excerpts[:3])[: self.max_excerpt_chars],
                    "interpretation_limit": (
                        "media-body corroboration only; this event may trigger further research "
                        "but does not by itself establish causality, electoral advantage, or a verified A/B fact"
                    ),
                }
            )

        return {
            "events": events,
            "stats": {
                "body_lead_count": len(evidence_items),
                "event_count": len(events),
                "corroborated_event_count": sum(
                    event.get("verification_status") == "corroborated_media"
                    for event in events
                ),
                "single_source_event_count": sum(
                    event.get("verification_status") == "single_source_media"
                    for event in events
                ),
                "ignored": ignored,
            },
        }



def campaign_event_research_questions(
    events: Iterable[Dict[str, Any]],
    jurisdiction: str,
) -> List[str]:
    """Create focused follow-up questions only from corroborated media events."""
    questions: List[str] = []
    for event in events or []:
        if str(event.get("verification_status") or "") != "corroborated_media":
            continue
        event_type = str(event.get("event_type") or "campaign_update")
        date = str(event.get("event_date") or event.get("date") or "")
        candidates = [str(value) for value in (event.get("candidate_entities") or []) if str(value)]
        locations = [str(value) for value in (event.get("locations") or []) if str(value)]
        subject = "、".join(candidates[:3]) or "相关候选人"
        place = "、".join(locations[:3]) or jurisdiction
        questions.append(
            f"{jurisdiction} {date} 关于{subject}在{place}的{event_type}媒体报道，"
            "是否存在官方资料、当事人原始声明或其他高等级来源可进一步确认？"
        )
    return list(dict.fromkeys(questions))
