# 当前候选人—政党关系双来源验证

`runtime.current_relationship_verifier` 用于把中选会115年地方公职人员选举候选人登记资料中的“推荐之政党”转换为可审计的当前人物—组织关系。

门禁固定为两层：

1. 中选会当前候选人缓存提供 A 级官方登记记录；
2. 独立媒体正文必须在同一引用片段中同时出现候选人姓名和推荐政党，且来源等级为 C、`independence_key` 不得为 CEC。

只有两层同时满足时才生成 `local_relationship` 提案。搜索标题、摘要、未读正文、同一来源转载、只出现候选人但未出现政党的报道都不能通过。关系范围仅限“2026地方选举登记时的政党推荐”，不得外推为永久党籍、派系归属、组织动员能力、选票转移或胜负判断。

示例：

```bash
python -m runtime.current_relationship_verifier \
  --county 新竹縣 \
  --research-result cache/research/new-hsinchu-party-check.json

# 确认 dry-run 全部通过后才正式晋升
python -m runtime.current_relationship_verifier \
  --county 新竹縣 \
  --research-result cache/research/new-hsinchu-party-check.json \
  --apply
```

`--research-result` 使用现有自动研究模块的公开结果 JSON；其中引用必须来自已读取正文。若仍有缺少独立媒体交叉确认的候选人，命令以退出码 3 返回，并在 `unresolved` 中列出缺口。
