# knowledge/

最小充分地方知识库。

- `historical/<county>/claims.jsonl`：L2 历史主张；
- `local/<county>/relationships.jsonl`：L3 人物、组织、政治网络与地区关系；
- `local/<county>/candidates.jsonl`：候选人或政治人物事实档案；
- `local/<county>/issues.jsonl`：有日期与验证状态的地方议题；
- `counties/<county>/`：由权威记录生成的 package 索引与生产状态。

历史关系必须带 `time_scope`，当前关系必须带 `last_verified_at`。所有记录保留 `source/evidence`、关系类型、`scope_boundary` 与不确定性；历史关系不得自动外推为当前关系。

`counties/` 已为 22 县市生成统一十主题 research plan。空索引与 unresolved 是合法状态，表示公开资料尚不足，而不是系统失败。generated package 不是新的事实来源；分析仍以 `historical/` 和 `local/` 的已晋升记录为准。

22 县市已有 2025 年底户籍人口及 2021 年主要从业行业的 A 级官方基线；
高雄市、台南市另有时间限定的学术历史事实，高雄市、台南市、新北市已有
双 C 级独立媒体验证的近期公开关系。中选会选区范围和历届结构在原始资料未能
解析前只保留为来源线索，不晋升具体结论。可复跑输入位于
`config/curated_county_baseline.yaml`，示例输入位于 `examples/county_knowledge_seeds/`。
