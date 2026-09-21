# data/elections/

历史选举事实库，按 election_type/year/jurisdiction 保存 JSONL。

```text
data/elections/<election_type>/<year>/<jurisdiction>.jsonl
```

每行符合 `schemas/election_record.yaml`，至少包含 township_district 层级。
不得放置未经核实的动态资料或伪造数据。
