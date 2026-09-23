"""Turn a validated Analysis Context into a Feishu-friendly answer."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from .models import ParsedRequest
from .router import CAMPAIGN_UPDATE, FULL_ANALYSIS, HELP, POLL_ANALYSIS, SOURCES, VERSION


SYSTEM_INSTRUCTIONS = """你是“小笠原选情分析机器人”的报告写作层。
你只能根据提供的 Analysis Context 和用户问题回答，不得自行补充未在 Context 中出现的政治事实。
必须区分事实、分析、竞选阵营主张与未知信息；资料不足时明确写 unknown/资料不足。
GDELT 检索只返回新闻标题、链接及首次发现时间；lead_only 标题不能当作已核实事件、文章内容或民调数字。
说明实时检索是否可用；引用当前线索时附原始链接与发现时间，明确仍待核实。
不得输出自主胜负预测、当选概率、候选人排名、政治推荐或投票建议。
完整分析先写截至 as_of 的当前选战状态和最近变化，再用历史结构解释；不要从历史沿革开始。
不同机构、不同方法或不同题型的民调不得拼接成趋势。
默认使用简洁中文，适合飞书群聊。"""


def help_text() -> str:
    return (
        "**小笠原选情分析机器人**\n\n"
        "可以直接问：\n"
        "- 分析高雄选情：运行完整 V1.4 结构分析\n"
        "- 高雄最近7天有什么变化：更新 Campaign State\n"
        "- 高雄最新民调怎么看：查看民调校准\n"
        "- 回复上一条分析问“为什么凤山重要？”：沿用线程上下文\n"
        "- 查看来源：列出当前分析使用的来源\n\n"
        "群聊默认需要 @机器人；私聊可直接提问。"
    )


def _analysis_payload(context: Dict[str, Any]) -> Dict[str, Any]:
    analysis = context.get("analysis_context", {}) if isinstance(context, dict) else {}
    manifest = context.get("analysis_manifest", {}) if isinstance(context, dict) else {}
    return {
        "task": analysis.get("task", {}),
        "readiness": analysis.get("readiness", {}),
        "campaign_state": analysis.get("campaign_state", {}),
        "current_candidates": analysis.get("current_candidates", []),
        "current_events": analysis.get("current_events", []),
        "polls": analysis.get("polls", []),
        "electoral_swing": analysis.get("electoral_swing", []),
        "split_ticket": analysis.get("split_ticket", []),
        "candidate_residuals": analysis.get("candidate_residuals", []),
        "spatial_anomalies": analysis.get("spatial_anomalies", []),
        "local_knowledge": analysis.get("local_knowledge", {}),
        "historical_baseline": analysis.get("historical_baseline", {}),
        "evidence_summary": analysis.get("evidence_summary", {}),
        "unknowns": analysis.get("unknowns", []),
        "warnings": analysis.get("warnings", []),
        "sources": analysis.get("sources", []),
        "manifest": {
            "skill_version": manifest.get("skill_version"),
            "created_at": manifest.get("created_at"),
        },
    }


class BaseReportWriter:
    async def write(self, request: ParsedRequest, context: Dict[str, Any]) -> str:
        raise NotImplementedError


class DeterministicReportWriter(BaseReportWriter):
    """Useful fallback when no OpenAI API key is configured."""

    async def write(self, request: ParsedRequest, context: Dict[str, Any]) -> str:
        if request.intent == HELP:
            return help_text()
        if request.intent == VERSION:
            version = context.get("analysis_manifest", {}).get("skill_version") or "1.4.0"
            return f"小笠原选情分析 Skill：**v{version}**。飞书机器人开发版：**v0.2**。"

        payload = _analysis_payload(context)
        state = payload.get("campaign_state") or {}
        readiness = payload.get("readiness") or {}
        focus = request.focus
        lines = [
            f"**{focus.jurisdiction or '选情'}｜结构化分析摘要**",
            f"截至：{state.get('as_of') or payload.get('manifest', {}).get('created_at') or 'unknown'}",
            f"数据状态：{readiness.get('status') or 'unknown'}",
        ]
        windows = state.get("windows") or {}
        if windows:
            lines.append(
                "近期事件："
                + "；".join(
                    f"{key} {value.get('event_count', 0)}项"
                    for key, value in windows.items()
                    if isinstance(value, dict)
                )
            )
        candidates = payload.get("current_candidates") or []
        if candidates:
            lines.append(f"当前候选人记录：{len(candidates)}条")
        polls = payload.get("polls") or []
        retrieval = (payload.get("evidence_summary") or {}).get("retrieval") or {}
        leads = state.get("retrieval_leads") or []
        if retrieval.get("backend") == "gdelt_doc_news":
            if not retrieval.get("available", True):
                lines.append("实时新闻检索：本次未成功；以下不代表最新选情。")
            elif leads:
                lines.append(f"实时新闻检索：发现{len(leads)}条待核实标题线索（非已证实选战事件）。")
                for item in leads[:5]:
                    lines.append(
                        f"- {item.get('title') or '无标题'}｜首次发现："
                        f"{item.get('first_seen_at') or '未知'}｜{item.get('url') or ''}"
                    )
            else:
                lines.append("实时新闻检索：未发现匹配标题；不能据此断定近期没有选战变化。")
        elif retrieval.get("backend") == "disabled":
            lines.append("实时新闻检索：未启用；当前摘要不能代表最新新闻全貌。")
        if request.intent == POLL_ANALYSIS:
            lines.append(f"民调记录：{len(polls)}份")
            lines.append(
                "当前民调校准可用："
                + str((payload.get("evidence_summary") or {}).get("current_poll_calibration_available", False))
            )
        if request.intent == SOURCES:
            sources = payload.get("sources") or []
            if not sources:
                lines.append("当前 Context 未记录可展示来源。")
            else:
                lines.append("来源：")
                for item in sources[:12]:
                    lines.append(
                        f"- {item.get('source_id') or 'unknown'} "
                        f"[{item.get('source_grade') or '?'}] "
                        f"{item.get('reference') or ''}"
                    )
        unknowns = payload.get("unknowns") or []
        if unknowns:
            lines.append("资料限制：" + "；".join(str(value) for value in unknowns[:4]))
        lines.append(
            "说明：当前未配置 OPENAI_API_KEY，因此显示确定性的结构化摘要；"
            "配置 API 后将由 LLM 基于同一 Analysis Context 生成自然语言研判。"
        )
        return "\n\n".join(lines)


class OpenAIReportWriter(BaseReportWriter):
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def _write_sync(self, request: ParsedRequest, context: Dict[str, Any]) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        payload = _analysis_payload(context)
        mode_hint = {
            FULL_ANALYSIS: "完整分析，优先当前选战，控制在约1200—2200字。",
            CAMPAIGN_UPDATE: "重点回答与近期/上一快照相比发生了什么，控制在约600—1200字。",
            POLL_ANALYSIS: "只重点解释民调方法、可比性、未决定比例及其与结构的关系。",
            SOURCES: "简要说明判断依据，并列出最关键来源。",
        }.get(request.intent, "回答用户追问，优先复用现有 Context，不重复整篇报告。")

        response = client.responses.create(
            model=self.model,
            store=False,
            instructions=SYSTEM_INSTRUCTIONS,
            input=(
                f"用户问题：{request.text}\n"
                f"任务模式：{mode_hint}\n"
                "Analysis Context(JSON)：\n"
                + json.dumps(payload, ensure_ascii=False)
            ),
        )
        text = str(getattr(response, "output_text", "") or "").strip()
        if not text:
            raise RuntimeError("OpenAI Responses API returned empty output_text")
        return text

    async def write(self, request: ParsedRequest, context: Dict[str, Any]) -> str:
        if request.intent == HELP:
            return help_text()
        return await asyncio.to_thread(self._write_sync, request, context)


def build_report_writer(api_key: str = "", model: str = "gpt-5.6-sol") -> BaseReportWriter:
    if api_key:
        return OpenAIReportWriter(api_key=api_key, model=model)
    return DeterministicReportWriter()
