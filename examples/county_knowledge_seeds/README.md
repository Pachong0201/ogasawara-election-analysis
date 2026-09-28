# 首批县市知识 seed

本目录保存高雄市、台南市与新北市首批可复跑的官方资料 lead 与 structured proposal。
这些记录只建立行政区及边界解释所需的基础背景，不推断政党支持、派系作用、个人投票选择或选举结果。

固定流程：

```bash
python3 -m runtime.cli knowledge-ingest --county "高雄市" \
  --retrieval-inbox examples/county_knowledge_seeds/高雄市/retrieval.jsonl
python3 -m runtime.cli knowledge-promote --county "高雄市" \
  --proposal-inbox examples/county_knowledge_seeds/高雄市/proposals.jsonl
python3 -m runtime.cli knowledge-production --counties "高雄市" --incremental
```

台南市与新北市使用相同流程。所有 lead 均先进入 `cache/retrieval/`；长期记录必须通过 evidence、independence、contradiction、time_scope、freshness 与 idempotence gates，并生成 promotion receipt。
