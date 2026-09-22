# V1.3 知识晋升示例

本目录仅演示数据结构，不包含真实政治判断，也不是 Skill 运行依赖。

## 1. 导入检索 lead

```bash
python -m runtime.cli knowledge-ingest \
  --county "示例县" \
  --retrieval-inbox examples/knowledge_promotion/retrieval.example.jsonl
```

## 2. Dry-run proposal

```bash
python -m runtime.cli knowledge-promote \
  --county "示例县" \
  --proposal-inbox examples/knowledge_promotion/proposal.example.jsonl \
  --dry-run
```

## 3. 正式晋升

移除 `--dry-run` 后，符合门禁的记录才会进入 `knowledge/`，同时生成 promotion receipt 与 county package。

示例使用 B 级已验证来源，只为了说明字段关系。真实任务必须按 `config/source_priority.yaml` 和 `config/knowledge_promotion.yaml` 判定来源等级、独立性、相反证据与时间有效性。
