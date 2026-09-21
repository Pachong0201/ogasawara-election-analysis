# 小笠原选举分析 Skill

`ogasawara-election-analysis` 是一个面向台湾选举的结构化分析 Skill。它借鉴小笠原欣幸公开展示的选举研究方法，重点回答“当前选举结构为什么形成”，而不是预测谁当选。

## 核心原则

> 先建立历史正常状态，再寻找异常；先发现异常，再解释异常。

分析路径固定为：

`历史基准 → 跨届变化 → 跨层级差异 → 空间异常 → 候选人残差 → 地方知识验证 → 民调校准 → 结构判断`

禁止把“最新民调 → 直接判断当前选情”当作主要路径。

## 适用输出

- 县市长、总统、区域立委及地方选举的结构分析；
- 胶着选区、异常票型、跨层级差异解释；
- 候选人地方基础、组织网络、派系与第三势力票源分析；
- 现任交棒与接班结构分析；
- 民调结构性校准。

不输出：

- 胜率、当选概率、胜负预测；
- 候选人排名、综合评分或推荐；
- 将政党票直接换算为县市长票；
- 将蓝白票机械相加。

## 文件结构

```text
ogasawara-election-analysis/
├─ SKILL.md
├─ README.md
├─ config/
│  ├─ source_priority.yaml
│  ├─ evidence_grades.yaml
│  ├─ analysis_thresholds.yaml
│  ├─ knowledge_layers.yaml
│  ├─ data_sources.yaml
│  ├─ freshness.yaml
│  └─ runtime.yaml
├─ rules/
│  ├─ historical_baseline.yaml
│  ├─ residual_analysis.yaml
│  ├─ poll_rules.yaml
│  ├─ local_knowledge_rules.yaml
│  ├─ writing_rules.yaml
│  ├─ data_acquisition.yaml
│  └─ political_neutrality.yaml
├─ schemas/
│  ├─ election_record.yaml
│  ├─ candidate.yaml
│  ├─ poll.yaml
│  ├─ political_claim.yaml
│  └─ local_relationship.yaml
├─ methods/
│  ├─ electoral_swing.md
│  ├─ split_ticket.md
│  ├─ candidate_residual.md
│  ├─ spatial_divergence.md
│  ├─ incumbent_transfer.md
│  └─ third_force.md
├─ runtime/             # V1.1 数据与运行层
│  ├─ data_readiness.py
│  ├─ election_loader.py
│  ├─ election_normalizer.py
│  ├─ matrix_builder.py
│  ├─ metrics.py
│  ├─ knowledge_loader.py
│  ├─ freshness.py
│  ├─ analysis_context.py
│  └─ cli.py
├─ data/                # 稳定历史选举事实与行政区版本
├─ knowledge/           # 最小充分地方知识缓存
├─ cache/               # 动态资料缓存
├─ references/
│  └─ ogasawara/
├─ examples/
│  └─ yilan/            # 只作为测试用例，不是运行依赖
└─ tests/
```

## 使用方式

### V1.0 方法层

1. 将 `SKILL.md` 作为系统指令加载。
2. 用户提出任务后，读取 `config/analysis_thresholds.yaml` 确认最低数据要求。
3. 按 `rules/historical_baseline.yaml` 建立历史基准。
4. 使用 `methods/` 中的公式和检查清单计算残差。
5. 命中异常筛选条件后，读取 `rules/local_knowledge_rules.yaml` 触发地方知识检索。
6. 最后才读取民调资料，按 `rules/poll_rules.yaml` 校准。
7. 按 `SKILL.md` 的统一模板输出，并明确证据等级和不确定性。

### V1.1 数据与运行层

```text
用户任务
→ Data Readiness Check
→ 本地历史选举数据
→ 缺失且 ONLINE：ElectionLoader 调用 Source Adapter
→ 标准化、校验、持久化
→ Matrix Builder
→ Metrics
→ 地方知识检索
→ Freshness
→ Analysis Context
→ 按 V1.0 输出
```

常用命令：

```bash
python -m runtime.cli readiness --county "宜兰县" --year 2026 --type county_mayor
python -m runtime.cli build-matrix --county "宜兰县" --year 2026 --type county_mayor
python -m runtime.cli context --county "宜兰县" --year 2026 --type county_mayor --write-manifest
```

## 最低数据要求

### 县市长分析

- 地方首长选举：最近至少三届；
- 总统选举：最近至少三届；
- 区域立委：原则上加入最近三届；若研究重点与立委无直接关系，可降低优先级，但不得完全忽视候选人或地方政治网络与区域立委选举之间的关系。

以 2026 县市长分析为例：

- 县市长：2014、2018、2022；
- 总统：2016、2020、2024；
- 区域立委：2016、2020、2024。

### 地理粒度

县市长分析不得只使用全县市总票。最低粒度为乡镇市区。

符合以下任一条件时，应下钻至村里或投开票所：

- 某乡镇与历史票型明显不一致；
- 相邻乡镇出现显著不同走势；
- 某候选人在特定乡镇异常强；
- 地方政治资料指向具体基层区域；
- 需要验证地方派系或候选人经营基础；
- 用户明确要求微观选区分析。

## 资料来源优先级

| 等级 | 资料类型 | 用途 |
|---|---|---|
| A | 中选会、地方选委会、政府正式统计、法院公开资料 | 事实基准；可确认结构性判断 |
| B | 学术研究、正式研究报告、方法透明的研究机构资料 | 结构性解释；与 A/C 交叉验证 |
| C | 可靠媒体的独立采访、调查或长期地方报道 | 地方知识；需至少两个独立来源 |
| D | 政党、候选人、竞选团队、相关利益方自行发布 | 仅作为线索或事件记录 |
| E | 社交媒体、匿名消息、未经验证转述 | 仅作为待验证线索 |

D 级和 E 级资料不得单独形成结构性判断。

## 历史知识与当前知识

任何历史政治关系必须记录 `time_scope`，例如：

- 1990 年代存在某派系，只能说明该派系在该历史时期具有影响；
- 不得自动写成“该派系目前仍主导当地政治”。

历史知识与当前知识必须逻辑分离。当前政治关系优先使用近 5—10 年资料，并以近期公开证据重新确认。

## 测试

`examples/yilan/` 是验收测试资料，不是运行依赖。删除该目录后 Skill 仍应能分析其他县市。

运行内置契约测试：

```bash
python3 -m unittest discover -s tests -v
```

## 边界声明

本 Skill 不提供选举预测、候选人推荐或政治动员。所有结构性判断必须附带证据等级、时间范围和不确定性说明。无法解释时应输出 `unknown`，不得自行补齐因果链。
