# Spatial Divergence：空间异常识别

## 目的

从行政区内部差异、相邻地区差异和地理集中程度中，发现需要地方知识解释的票型。空间异常本身不是派系、买票或组织票的证据。

## 指标

### Spatial Variance

衡量行政区内部候选人得票离散程度：

`CV = standard_deviation(vote_share) / mean(vote_share)`

也可使用标准差、极差、四分位距或变异系数。记录选用的统计量与原因。

### Neighbor Divergence

衡量相邻行政区之间的差异：

`NeighborDivergence(i,j) = VoteShare(i) - VoteShare(j)`

或使用同一候选人的残差：

`NeighborDivergence(i,j) = Residual(i) - Residual(j)`

必须记录相邻关系依据，例如行政边界、共同生活圈、历史选区。

### Geographic Concentration

衡量候选人得票或残差是否高度集中于少数区域：

`Concentration = 票源前 N 个行政区得票 / 候选人总得票`

`N` 需明确。可与人口规模、行政区数量和候选人地缘基础一起判断。

## 处理顺序

1. 先确认行政区划、投票率、候选人数量与有效票结构可比。
2. 计算空间指标。
3. 记录异常地区、数值与方向。
4. 设置 `local_explanation_required=true`。
5. 进入 `rules/local_knowledge_rules.yaml`。
6. 不得先指定解释，例如“因为某派系”。
7. 无法验证时保留 `unknown`。

## 解释边界

- 高空间离散不等于派系。
- 相邻地区差异不等于组织动员。
- 地理集中不等于特定群体支持。
- 空间异常可能与候选人地缘、地方建设、产业、行政区划或投票率结构有关。
- 任何解释需要至少两个独立来源，或一个 A/B 级高质量来源。

## 输出建议

> 甲乡与乙乡相邻，但同一候选人得票率相差 X 个百分点，且该差异在过去三届同类选举中未稳定出现。此空间残差先标记为待解释，不直接归因于派系；后续检索地方组织与候选人经营资料。
