# Split Ticket：同日分裂投票

## 目的

在同日举行的总统与区域立委选举中，识别候选人得票是否明显偏离所属政党或其他票种表现。差值称为“候选人残差”或“跨票种残差”，不得直接称为“个人票”。

## 公式

总统与区域立委同日选举：

`SplitTicketResidual = LegislatorVoteShare - PresidentVoteShare`

加入不分区政党票：

`LegislatorPartyResidual = LegislatorVoteShare - PartyListVoteShare`

`PresidentPartyResidual = PresidentVoteShare - PartyListVoteShare`

可在县市、乡镇市区、村里或投开票所层级分别计算。

## 必备记录

- `region`
- `legislator_vote_share`
- `president_vote_share`
- `party_list_vote_share`
- `same_day`：是否为同日选举
- `candidate_party`
- `candidate_vs_party`
- `baseline_method`
- `source_grade`
- `boundary_version`

## 解释边界

残差只说明：

> 候选人在该票种上的表现明显高于或低于参考基准。

不能自动说明原因属于：

- 个人魅力；
- 派系票；
- 地方组织票；
- 跨党支持；
- 中间选民支持。

这些原因必须另行通过候选人地方基础、地方组织、公开支持、地方议题或合格民调交叉表验证。

## 常见误用

错误写法：

> 某候选人立委票比总统票多 10 个百分点，说明有 10% 个人票。

正确写法：

> 某候选人区域立委得票率较同日同党总统得票率出现约 10 个百分点的正残差；这显示其表现偏离该票种基准，但残差来源需进一步验证，不能直接认定为个人票。

## 资料限制

- 若总统与立委并非同日选举，不得直接相减。
- 若选区边界不同，必须先聚合到共同行政区。
- 若不同票种的有效票结构差异很大，必须记录。
- 不得以单一残差推断买票、违法或组织动员。
