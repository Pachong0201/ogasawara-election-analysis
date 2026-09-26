"""Adapter from bot intents to the existing V1.4 AnalysisPipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

from runtime.models import ElectionTask
from runtime.pipeline import AnalysisPipeline
from runtime.source_registry import RetrievalBackend

from .models import ElectionFocus


class SkillService:
    def __init__(
        self,
        repo_root: Path,
        mode: str = "online",
        retrieval_backend: Optional[RetrievalBackend] = None,
    ):
        self.repo_root = Path(repo_root)
        self.mode = mode
        self.retrieval_backend = retrieval_backend

    def _run_sync(self, focus: ElectionFocus) -> Dict[str, Any]:
        pipeline = AnalysisPipeline(
            repo_root=self.repo_root,
            mode=self.mode,
            retrieval_backend=self.retrieval_backend,
        )
        context = pipeline.run(
            ElectionTask(
                election_type=focus.election_type,
                target_year=int(focus.target_year),
                jurisdiction=focus.jurisdiction,
                analysis_level=focus.analysis_level,
            ),
            allow_online=self.mode != "offline",
            write_manifest=False,
        )
        return context.to_dict()

    async def run(self, focus: ElectionFocus) -> Dict[str, Any]:
        return await asyncio.to_thread(self._run_sync, focus)
