"""Deterministic first-pass intent router.

The router deliberately stays simple and auditable. An LLM can be added later
as a fallback, but core county/city selection and action routing must not
depend on a model being available.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from .models import ElectionFocus, ParsedRequest


HELP = "help"
VERSION = "version"
FULL_ANALYSIS = "full_analysis"
CAMPAIGN_UPDATE = "campaign_update"
POLL_ANALYSIS = "poll_analysis"
SOURCES = "sources"
CONTEXT_QA = "context_qa"


ALIASES: Dict[str, str] = {
    "台北": "台北市", "臺北": "台北市", "台北市": "台北市", "臺北市": "台北市",
    "新北": "新北市", "新北市": "新北市",
    "桃园": "桃园市", "桃園": "桃园市", "桃园市": "桃园市", "桃園市": "桃园市",
    "台中": "台中市", "臺中": "台中市", "台中市": "台中市", "臺中市": "台中市",
    "台南": "台南市", "臺南": "台南市", "台南市": "台南市", "臺南市": "台南市",
    "高雄": "高雄市", "高雄市": "高雄市",
    "基隆": "基隆市", "基隆市": "基隆市",
    "新竹市": "新竹市",
    "嘉义市": "嘉义市", "嘉義市": "嘉义市",
    "新竹县": "新竹县", "新竹縣": "新竹县",
    "苗栗": "苗栗县", "苗栗县": "苗栗县", "苗栗縣": "苗栗县",
    "彰化": "彰化县", "彰化县": "彰化县", "彰化縣": "彰化县",
    "南投": "南投县", "南投县": "南投县", "南投縣": "南投县",
    "云林": "云林县", "雲林": "云林县", "云林县": "云林县", "雲林縣": "云林县",
    "嘉义县": "嘉义县", "嘉義縣": "嘉义县",
    "屏东": "屏东县", "屏東": "屏东县", "屏东县": "屏东县", "屏東縣": "屏东县",
    "宜兰": "宜兰县", "宜蘭": "宜兰县", "宜兰县": "宜兰县", "宜蘭縣": "宜兰县",
    "花莲": "花莲县", "花蓮": "花莲县", "花莲县": "花莲县", "花蓮縣": "花莲县",
    "台东": "台东县", "臺東": "台东县", "台东县": "台东县", "臺東縣": "台东县",
    "澎湖": "澎湖县", "澎湖县": "澎湖县", "澎湖縣": "澎湖县",
    "金门": "金门县", "金門": "金门县", "金门县": "金门县", "金門縣": "金门县",
    "连江": "连江县", "連江": "连江县", "连江县": "连江县", "連江縣": "连江县",
}


def _clean_text(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"^@\S+\s*", "", text)
    return text.strip()


def _jurisdiction(text: str) -> str:
    matches = [(alias, canonical) for alias, canonical in ALIASES.items() if alias in text]
    if not matches:
        return ""
    return max(matches, key=lambda item: len(item[0]))[1]


def _window(text: str) -> Optional[int]:
    if re.search(r"(最近)?\s*7\s*天|一周|本周|这周|這周", text):
        return 7
    if re.search(r"(最近)?\s*14\s*天|两周|兩周", text):
        return 14
    if re.search(r"(最近)?\s*30\s*天|一个月|一個月|本月", text):
        return 30
    return None


class IntentRouter:
    def __init__(self, default_target_year: int = 2026):
        self.default_target_year = int(default_target_year)

    def parse(self, text: str, previous_focus: Optional[ElectionFocus] = None) -> ParsedRequest:
        cleaned = _clean_text(text)
        previous_focus = previous_focus or ElectionFocus(target_year=self.default_target_year)
        jurisdiction = _jurisdiction(cleaned) or previous_focus.jurisdiction
        focus = ElectionFocus(
            jurisdiction=jurisdiction,
            election_type=previous_focus.election_type or "county_mayor",
            target_year=previous_focus.target_year or self.default_target_year,
            analysis_level=previous_focus.analysis_level or "township_district",
        )

        if any(token in cleaned.lower() for token in ("help", "/help")) or any(
            token in cleaned for token in ("帮助", "幫助", "怎么用", "怎麼用")
        ):
            intent = HELP
        elif any(token in cleaned for token in ("版本", "/version", "version")):
            intent = VERSION
        elif any(token in cleaned for token in ("来源", "來源", "依据", "依據", "证据", "證據")):
            intent = SOURCES
        elif "民调" in cleaned or "民調" in cleaned:
            intent = POLL_ANALYSIS
        elif any(token in cleaned for token in ("更新", "变化", "變化", "最近", "这周", "這周", "本周")):
            intent = CAMPAIGN_UPDATE
        elif any(token in cleaned for token in ("分析", "选情", "選情", "研判", "完整")):
            intent = FULL_ANALYSIS
        else:
            intent = CONTEXT_QA

        return ParsedRequest(
            intent=intent,
            text=cleaned,
            focus=focus,
            window_days=_window(cleaned),
            refresh=any(token in cleaned for token in ("更新", "最新", "现在", "現在", "重新")),
        )


def looks_long_running(text: str) -> bool:
    cleaned = _clean_text(text)
    return any(
        token in cleaned
        for token in ("分析", "选情", "選情", "研判", "更新", "最近", "民调", "民調")
    )
