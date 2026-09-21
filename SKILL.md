---
name: ogasawara-election-analysis
description: 借鉴小笠原欣幸公开选举研究方法的台湾选举结构化分析 Skill。先建立历史基准，再寻找跨届、跨层级、空间与候选人残差，最后以地方知识与民调校准。不输出胜负预测、胜率或候选人排名。
version: 1.1.0
language: zh-TW
entrypoint: SKILL.md
---

# 小笠原选举分析 Skill

## 一、运行时核心指令

> 分析台湾选举时，首先建立历史选举基准，不以单一民调、单届选举或政党标签直接形成结论。将总统、立委、地方首长和必要的政党票进行跨届、跨层级比较，在乡镇市区层面寻找时间、空间、候选人及票种之间的异常残差。
>
> 数据异常只用于提出研究问题，不直接构成因果解释。发现异常后，应检索候选人地方经营、当前地方政治人物、组织网络、历史研究及地方议题等资料进行验证。
>
> 所有历史政治关系必须具有时间范围；历史派系、人物和组织不得自动外推到当前。所有当前政治关系必须以近期公开证据重新确认。
>
> 民调用于校准当前状态，而不是替代历史结构。不同调查机构、不同调查方式的数据不得未经说明直接计算趋势。
>
> 最终分析应回答当前选举结构如何形成、哪些选票或地区正在变化、有哪些解释得到证据支持、哪些仍无法确认。不得输出自主的胜负概率、候选人评分或政治推荐。

## 二、适用任务

优先用于：

- 县市长、总统、区域立委及地方选举的结构分析；
- 解释某县市、乡镇市区为何成为胶着或异常选区；
- 比较两个或多个县市、乡镇的选举结构；
- 分析候选人地方基础、地方组织、派系与跨党支持；
- 分析蓝绿白票源重新分配、第三势力票流动；
- 分析总统票、立委票、政党票、县市长票之间的分裂投票；
- 分析现任交棒、接班候选人及其组织移转；
- 对民调进行结构性解释，而不是直接预测。
- 依据票型异常触发最小充分地方知识检索。

## 三、不承担的任务

不得以以下内容作为主要输出：

- 候选人胜率、当选概率、胜负预测；
- “谁最有可能当选”式结论或候选人排名；
- 将政党票直接换算成县市长票；
- 将蓝白票机械相加；
- 依据单一民调形成确定结论；
- 对候选人进行综合评分、推荐或政治动员。

## 四、分析路径

所有分析必须遵循：

`历史基准` → `跨届变化` → `跨层级差异` → `空间异常` → `候选人残差` → `地方知识验证` → `民调校准` → `结构判断`

不得采用：

`最新民调` → `直接判断当前选情`

## 五、运行模式与执行流程

### 运行模式

#### ONLINE MODE

存在可用联网或搜索工具。允许：

- 调用已注册的 `ElectionDataSource` 补齐缺失的稳定历史选举数据；
- 调用已注册的 `RetrievalBackend` 进行最小充分地方知识检索；
- 验证当前候选人、民调、公开支持、政党合作与竞选事件的新鲜度。

禁止：

- 每次调用都覆盖已验证历史数据；
- 绕过 `runtime/source_registry.py` 直接写死特定网站爬虫；
- 未经验证就把搜索结果写入长期知识库。

#### OFFLINE MODE

无联网能力。只能使用：

- 本地历史选举数据；
- 本地 `knowledge/` 知识库；
- 本地 `cache/` 中仍在有效期内的动态资料；
- 用户在本轮任务提供的资料。

若必要资料缺失：

- 不得伪装已完成联网检索；
- 输出 `analysis_status: insufficient_data`；
- 列出缺失项。

### STEP 0：Data Preparation

在任何完整选举结构分析开始前，先执行 Data Readiness Check。

流程：

1. 识别 `ElectionTask`：选举类型、目标年份、地区、分析层级、候选人。
2. 调用 `runtime/data_readiness.py` 检查本地数据。
3. 稳定历史数据优先读取 `data/elections/`；本地缺失且运行环境允许联网时，从 `config/data_sources.yaml` 的高优先级来源补齐并持久化。
4. 动态资料必须检查 freshness，过期后重新验证。
5. 数据不足时生成 Data Readiness Report，并标记 `ready`、`partial` 或 `insufficient`。
6. 若 HARD REQUIRED 数据仍缺失，不得进行完整结构判断。
7. 分析前生成 `Analysis Context`，LLM 只能基于该 context 生成最终结构分析。
8. 每次运行生成 `analysis_manifest.json`，记录数据来源、文件、指标基准、新鲜度、缺失与警告。

关联文件：

- `runtime/data_readiness.py`
- `runtime/election_loader.py`
- `runtime/election_normalizer.py`
- `runtime/freshness.py`
- `runtime/analysis_context.py`
- `config/data_sources.yaml`
- `config/freshness.yaml`
- `config/runtime.yaml`
- `rules/data_acquisition.yaml`

### STEP 1：识别选举任务

先确认：

- 选举类型：总统、区域立委、不分区政党票、县市长、县市议员、乡镇市长等；
- 地区与行政区层级；
- 当前选举周期；
- 是否涉及特定候选人；
- 输出粒度：县市整体、乡镇市区、村里或投开票所；
- 可用资料的时间范围与证据等级。

读取 `config/analysis_thresholds.yaml` 确认最低数据要求。

### STEP 2：建立历史政治基准

按 `rules/historical_baseline.yaml` 执行。

- 同类型选举至少读取最近三届；有资料时优先四届以上。
- 县市长分析必须建立乡镇市区矩阵；不得只看全县市总票。
- 总统、区域立委按同一行政区矩阵对齐。
- 对每个地区记录得票率、投票率、候选人、政党、边界变更和资料来源。
- 以区间、中位数或典型走势描述正常状态，不写“某乡基本盘就是 XX%”。

### STEP 3：计算 Electoral Swing

按 `methods/electoral_swing.md` 执行。

至少计算：

- 县市总体摆动；
- 各乡镇市区摆动；
- 与全县市平均摆动之差（地方特有摆动）；
- 投票率变化。

公式：

`Swing(i,t) = VoteShare(i,t) - VoteShare(i,t-1)`

`LocalSwing(i,t) = Swing(i,t) - Swing(region,t)`

### STEP 4：执行跨层级比较

按 `methods/split_ticket.md` 执行。

至少比较：

- 县市长票 ↔ 同期或最近总统票；
- 区域立委票 ↔ 同日总统票；
- 必要时加入政党票。

识别 `Local-National Gap`。若长期存在明显差异，进入候选人、地方组织、派系、治理评价、对手强弱和地方议题检索。

### STEP 5：分析同日分裂投票

同日总统与区域立委选举计算：

`SplitTicketResidual = LegislatorVoteShare - PresidentVoteShare`

有政党票时计算：

`LegislatorVoteShare - PartyListVoteShare`

不得将差值直接称为“个人票比例”；统一称为“候选人残差”或“跨票种残差”。

### STEP 6：Candidate Residual 分析

按 `methods/candidate_residual.md` 执行。

`CandidateResidual = CandidateVoteShare - ReferenceBaseline`

`ReferenceBaseline` 可选：

- 同日总统票；
- 同日政党票；
- 前后两次总统选举平均；
- 同党历史平均；
- 县市整体趋势。

必须记录 `baseline_method`。不同基准不得混用而不说明。

### STEP 7：空间异常识别

按 `methods/spatial_divergence.md` 执行。

计算并记录：

- `SpatialVariance`：行政区内部候选人得票离散程度；
- `NeighborDivergence`：相邻行政区之间的票型差异；
- `GeographicConcentration`：候选人票是否高度集中于少数区域。

命中 `config/analysis_thresholds.yaml` 的异常筛选条件时，标记：

`local_explanation_required=true`

然后进入 `rules/local_knowledge_rules.yaml` 的最小充分地方知识检索流程。

### STEP 8：地方知识检索

触发后按以下阶段检索：

1. 候选人：出生地、过去选区、曾任职务、历次选举、服务处、长期经营地区；
2. 基层政治：地方议员、乡镇市长、地方政治人物、公开支持关系；
3. 地方组织：学术资料确认的地方派系、农渔会、地方社团、历史政治组织；
4. 历史研究：地方志、学术论文、县市政治研究、乡镇个案；
5. 当前验证：对历史人物、派系和组织寻找近 5—10 年资料，确认政治连续性。

停止条件见 `rules/local_knowledge_rules.yaml`。无法满足时，输出 `unknown` 或“现有公开资料不足以确定原因”。

### STEP 9：民调校准

民调模块永远放在结构分析之后。使用前按 `rules/poll_rules.yaml` 检查：

`pollster`、`commissioner`、`method`、`sample_size`、`sample_frame`、`field_start`、`field_end`、`moe`、`moe_applicable`、`undecided`。

民调只用于检验当前状态是否偏离历史结构，不得替代历史结构。

### STEP 10：形成结构判断

优先检查以下五类机制，并分别标记内部证据等级：

| 机制 | 说明 |
|---|---|
| Party Structure | 政党长期基本结构 |
| Candidate Effect | 候选人经营、知名度、跨党支持 |
| Local Organization | 地方公职、政治人物、基层组织 |
| Third-force Redistribution | 第三势力及中间票重新组合 |
| Issue / Governance Effect | 地方治理评价、重大地方议题、政策 |

证据等级仅用于内部管理，不得转换为候选人政治评分、排名或胜负判断。

## 六、默认输出格式

县市分析采用以下结构。核心判断 150—250 字，第一句不得是“某候选人支持度 XX%”。

## 【县市名称】

### 核心判断

直接回答当前选举结构为什么形成。

### 一、历史政治版图如何形成

只写与当前选举有关的历史，标明时间范围。

### 二、与上一轮相比发生了什么变化

分析政党票、地方首长票、总统票、立委票与投票率。

### 三、变化主要发生在哪里

识别 3—8 个关键乡镇市区，说明选择依据。

### 四、候选人的地方政治基础

分析长期经营、历次参选、地方组织、个人残差。

### 五、第三势力和中间票如何重新组合

不得机械加票，不得直接写“第三势力票将转给某方”。

### 六、当前民调是否验证这种结构

比较调查方法及交叉表，指出未决定比例和样本限制。

### 七、为什么形成当前竞争结构

归纳 2—4 个决定性结构因素。

### 八、后续需要观察什么

只列可公开验证的变量。不得写“某候选人必须拿到多少票才能获胜”。

写作规范见 `rules/writing_rules.yaml`。

## 七、五层知识体系

Skill 调用的知识必须分成五层，禁止混用。完整定义见 `config/knowledge_layers.yaml`。

| 层级 | 名称 | 性质 | 关键时间字段 |
|---|---|---|---|
| L1 | 选举事实库 | 事实基准 | `election_year`、`boundary_version` |
| L2 | 历史政治知识库 | 历史背景 | `time_scope` |
| L3 | 当前地方政治知识库 | 当前地方政治 | `last_verified_at`、`time_scope` |
| L4 | 当前选举事件库 | 带日期事件 | `date` |
| L5 | 民调数据库 | 结构校准 | `field_start`、`field_end`、`publish_date` |

硬规则：

- 历史知识与当前知识必须物理或逻辑分离；
- 历史关系不得直接当成当前关系；
- 当前关系必须以近 5—10 年公开证据重新确认；
- 民调数据库只能在历史结构、残差分析和地方知识检索之后使用；
- L4 的事件叙事不得替代 L1 事实或 L5 民调方法检查。

## 八、证据与不确定性

证据等级 :

- A：官方原始资料，如中选会、地方选委会、政府正式统计、法院公开资料；
- B：学术研究、正式研究报告、方法透明的研究机构资料；
- C：可靠媒体的独立采访、调查或长期地方报道；
- D：政党、候选人、竞选团队、相关利益方自行发布；
- E：社交媒体、匿名消息、未经验证转述。

硬规则：D 级和 E 级资料不得单独形成结构性判断。

必须允许输出：

- `unknown`；
- “现有数据可以确认 A 现象，但尚不能确定原因”；
- “当前有两种可能解释，现有证据不足以区分”；
- “历史资料显示过去存在该政治网络，但未找到足够资料确认其在当前选举中仍具有相同作用”。

## 九、硬性禁止规则

完整清单见 `rules/political_neutrality.yaml`。任何情况下不得：

1. 把一次县市长高票直接定义为政党基本盘；
2. 把总统票直接当县市长票；
3. 把蓝白票直接相加；
4. 把现任满意度直接转成接班人支持；
5. 把不同机构民调连成趋势；
6. 把网络民调与电话民调直接计算升降；
7. 把统计残差直接称为个人票；
8. 把高空间离散直接解释成派系；
9. 根据异常票型推断买票或违法行为；
10. 根据年龄、族群、职业等群体资料直接推断个人政治选择；
11. 把历史派系关系直接外推到当前；
12. 使用单一竞选阵营说法形成结构性判断；
13. 输出自主胜负概率；
14. 给政治候选人进行综合评分、排名或推荐。

## 十、V1.0 / V1.1 范围

V1.0 方法层实现：

- 历史基准；
- Electoral Swing；
- Split Ticket；
- Candidate Residual；
- 地方知识触发机制；
- 民调规则；
- 统一输出模板。

V1.1 数据与运行层增加：

- Data Readiness Gate；
- 本地优先的 Election Loader、Normalizer；
- Matrix Builder 与 Metrics 实际计算；
- Freshness 与动态缓存；
- 最小充分地方知识检索；
- Analysis Context 与 analysis_manifest.json；
- CLI `readiness`、`build-matrix`、`metrics`、`context`。

第二阶段再增加村里／投票所空间分析、Neighbor Divergence 自动化、地方政治知识图谱、半自动历史知识检索和多县市横向比较。

`examples/yilan/` 只作为测试用例，不得成为 Skill 运行依赖。
