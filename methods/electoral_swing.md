# Electoral Swing

## 目的

区分“全台或全县市共同变化”与“地方特有变化”。摆动不是候选人评价，也不是胜负预测，只用于定位需要进一步解释的异常。

## 定义

第 `i` 个行政区在 `t` 届选举的得票率：

`VoteShare(i,t)`

跨届摆动：

`Swing(i,t) = VoteShare(i,t) - VoteShare(i,t-1)`

地方特有摆动：

`LocalSwing(i,t) = Swing(i,t) - Swing(region,t)`

其中 `Swing(region,t)` 为全县市、全选区或全国平均摆动。`LocalSwing` 用于区分全县市共同趋势与地方特有变化。

## 计算层级

至少计算：

1. 县市总体摆动；
2. 乡镇市区摆动；
3. 与全县市平均摆动之差；
4. 投票率变化；
5. 候选人数量变化与主要候选人结构变化。

资料允许时继续计算：

- 村里摆动；
- 投开票所摆动；
- 总统票、立委票、政党票、县市长票之间的同期摆动差异。

## 必备记录

| 字段 | 说明 |
|---|---|
| `region` | 行政区名称与代码 |
| `election_type` | 选举类型 |
| `period_t` | 本届 |
| `period_t_minus_1` | 上届 |
| `vote_share_t` | 本届得票率 |
| `vote_share_t_minus_1` | 上届得票率 |
| `swing` | 简单摆动 |
| `region_swing` | 全县市或全国平均摆动 |
| `local_swing` | 地方特有摆动 |
| `turnout_t` | 本届投票率 |
| `turnout_change` | 投票率变化 |
| `boundary_version` | 边界版本 |
| `baseline_method` | 基准方法 |
| `source_grade` | 证据等级 |

## 解释边界

- 摆动可能来自投票率变化、候选人结构变化、政党合作、第三势力参选或资料口径变化。
- 不得用单一摆动作因果结论。
- 不得把总统票摆动直接当作县市长票摆动。
- 行政区划变更或选区重划时必须先建立对应关系。
- 命中 `config/analysis_thresholds.yaml` 的筛选阈值时，标记 `local_explanation_required=true`，再检索地方知识。

## 输出示例

不写：

> 某乡基本盘为 55%，所以本届变化不大。

应写：

> 过去数届同类选举中，甲阵营在该乡通常维持约某一区间；本届摆动为 `X` 个百分点，较全县市平均多出 `Y` 个百分点，因此列为地方特有变化候选区，需进一步下钻。
