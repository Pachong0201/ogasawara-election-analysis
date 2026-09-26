# knowledge/

最小充分地方知识库。

- `historical/<county>/claims.jsonl`：L2 历史主张；
- `local/<county>/relationships.jsonl`：L3 人物、组织、政治网络与地区关系；
- `local/<county>/candidates.jsonl`：候选人或政治人物事实档案；
- `local/<county>/issues.jsonl`：有日期与验证状态的地方议题；
- `counties/<county>/`：由权威记录生成的 package 索引与生产状态。

历史关系必须带 `time_scope`，当前关系必须带 `last_verified_at`。所有记录保留 `source/evidence`、关系类型、`scope_boundary` 与不确定性；历史关系不得自动外推为当前关系。

`counties/` 已为 22 县市生成统一十主题 research plan。空索引与 unresolved 是合法状态，表示公开资料尚不足，而不是系统失败。generated package 不是新的事实来源；分析仍以 `historical/` 和 `local/` 的已晋升记录为准。

高雄市、台南市、新北市已有首批 A 级官方行政/空间背景记录及 promotion receipt。可复跑输入位于 `examples/county_knowledge_seeds/`。
