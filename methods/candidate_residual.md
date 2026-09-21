# Candidate Residual

## 目的

找出候选人是否明显偏离政党或其他参考基准，用于提出研究问题，而不是直接归因。

## 公式

`CandidateResidual = CandidateVoteShare - ReferenceBaseline`

`ReferenceBaseline` 可根据任务选择：

1. 同日总统票；
2. 同日政党票；
3. 前后两次总统选举平均；
4. 同党历史平均；
5. 县市整体趋势。

## 必备记录

每次计算必须记录：

- `baseline_method`；
- `baseline_periods`；
- `baseline_geography`；
- `boundary_version`；
- `source_grade`；
- `candidate_vote_share`；
- `reference_baseline_value`；
- `residual`；
- `residual_type=candidate_residual`；
- `local_explanation_required`。

不同基准不得混用而不说明。

## 解释边界

残差可用于：

- 发现候选人或地区异常；
- 与相邻地区比较；
- 判断是否需要地方知识检索；
- 检验民调是否偏离历史结构。

残差不可用于：

- 直接称为“个人票比例”；
- 直接称为“派系票”；
- 直接称为“组织票”；
- 直接推断跨党支持；
- 推断买票或违法；
- 输出候选人评分或排名。

## 地方知识触发

当候选人残差命中 `config/analysis_thresholds.yaml` 阈值时：

1. 标记 `local_explanation_required=true`；
2. 记录残差出现的具体乡镇市区；
3. 进入 `rules/local_knowledge_rules.yaml`；
4. 先检索候选人出生地、长期经营、服务处、历次参选；
5. 再检索地方公职、组织、派系与近期公开支持；
6. 寻找至少两个独立来源，或一个 A/B 级高质量来源；
7. 无法验证时输出 `unknown`。

## 示例表述

不写：

> 他有 22% 个人票。

应写：

> 该候选人本届县长得票率为 55%，与本次选用的同党总统得票基准 33% 相比，出现约 22 个百分点的候选人正残差。该数值只能说明其表现明显偏离所选政党基准，不能直接认定全部属于个人票；需进一步检查地方组织、跨党支持、对手结构与第三势力票流动。
