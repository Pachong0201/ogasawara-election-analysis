---
name: ogasawara-election-analysis
description: 借鉴小笠原欣幸公开选举研究方法的台湾选举结构化分析 Skill。以历史结构为基准，同时建立带 as_of 的选战进行时快照，识别最近30／14／7日变化并以地方知识与民调校准。不输出胜负预测、胜率或候选人排名。
version: 1.4.0
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
> 完整分析必须明确 as_of，先回答截至当前选战打到什么阶段、最近30／14／7日发生了哪些可验证变化、这些变化是否触及历史结构，再用历史基准解释。不得输出自主的胜负概率、候选人评分或政治推荐。

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

`历史基准` → `当前候选人格局` → `Campaign State Snapshot(as_of)` → `最近30／14／7日变化` → `历史／当前双触发地方知识检索` → `同源民调校准` → `当前竞争结构`

不得采用：

`最新民调` → `直接判断当前选情`

也不得采用：

`历史票型` → `忽略本轮选战最新变化`

## 五、运行模式与执行流程

### 运行模式

#### ONLINE MODE

存在可用联网或搜索工具。允许：

- 调用已注册的 `ElectionDataSource` 补齐缺失的稳定历史选举数据；
- 对 2014—2024 核心历史选举，优先使用内置 `cec_open_data` Adapter 读取中选会官方 `votedata.zip`；首次下载后使用本地缓存，不得每次重复抓取；
- 调用已注册的 `RetrievalBackend` 进行最小充分地方知识检索；
- 若宿主具备 Web/Search 工具，可将搜索结果按 `schemas/retrieval_lead.yaml` 写入 JSON/JSONL，并通过 `HostRetrievalBackend` 注入；
- 验证当前候选人、民调、公开支持、政党合作与竞选事件的新鲜度。

禁止：

- 每次调用都覆盖已验证历史数据；
- 绕过 `runtime/source_registry.py` 直接写死特定网站爬虫；
- 未经验证就把搜索结果写入长期知识库；
- 将 host retrieval lead（包括 verification_status=verified）自动升级成长期政治事实。

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
7. ONLINE 当前周期分析必须生成 `Campaign State Snapshot`，记录 `as_of`、7/14/30日事件窗口、同源民调变化与上一快照差异。
8. 历史异常和当前选战变化均可触发地方知识检索；不得要求先出现历史票型异常。
9. 分析前生成 `Analysis Context`，LLM 只能基于该 context 生成最终结构分析。
10. 每次运行生成 `analysis_manifest.json`，记录数据来源、文件、指标基准、新鲜度、选战快照、缺失与警告。

关联文件：

- `runtime/data_readiness.py`
- `runtime/election_loader.py`
- `runtime/election_normalizer.py`
- `runtime/freshness.py`
- `runtime/analysis_context.py`
- `runtime/campaign_state.py`
- `runtime/campaign_event.py`
- `runtime/pipeline.py`
- `runtime/host_retrieval.py`
- `runtime/knowledge_builder.py`
- `schemas/retrieval_lead.yaml`
- `schemas/campaign_event.yaml`
- `schemas/campaign_media_event.yaml`
- `schemas/knowledge_proposal.yaml`
- `schemas/knowledge_promotion_receipt.yaml`
- `config/knowledge_promotion.yaml`
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

### STEP 1A：Live Campaign State Snapshot

在进入历史解释前，先建立当前选战快照：

1. 明确 `as_of`，不得使用“目前”“近期”而无具体时点；
2. 读取当前候选人、登记／提名状态、竞选组织与已验证当前事件；
3. ONLINE MODE 主动检索最近 30 日资料；公开新闻检索结果若能读取正文，必须先经过 evidence extraction、候选人／地点实体识别与跨来源聚类，再形成 media Campaign Event；该对象服从 `schemas/campaign_media_event.yaml`，不得替代严格标准事件 `schemas/campaign_event.yaml`；
4. 单一媒体正文只保留为 single_source_media；两个以上独立来源正文对同一事件相互印证时可形成 corroborated_media，但只具有 research_trigger_only 资格，不得称为 A/B 级已核实事实；
5. 将已验证事件与结构化 Campaign Event 一并放入 7 日、14 日、30 日窗口；
6. 与上一份 Campaign State Snapshot 比较候选人格局、新事件、新检索线索与新民调；
7. 对同一 pollster、commissioner、method、sample_frame、question_wording 的连续调查计算 same-series point-estimate change；
8. 出现候选人格局变化、已验证重要竞选事件、corroborated_media 事件、同源民调明显变化或上一快照后的新增事件时，设置 `campaign_change_trigger=true`；
9. `campaign_change_trigger` 与历史票型异常具有同等“触发研究”资格，可进入 Minimum Sufficient Local Knowledge；
10. 任何 campaign trigger 都只表示“需要进一步验证”，不得直接解释为胜负变化或因果证明。

关联文件：

- `runtime/campaign_state.py`
- `config/runtime.yaml`
- `config/freshness.yaml`
- `rules/data_acquisition.yaml`
- `rules/poll_rules.yaml`

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

V1.4 起，即使没有历史票型异常，只要 `campaign_change_trigger=true`，也进入 `rules/local_knowledge_rules.yaml` 的最小充分地方知识检索流程。

### STEP 8：地方知识检索

触发后按以下阶段检索：

1. 候选人：出生地、过去选区、曾任职务、历次选举、服务处、长期经营地区；
2. 基层政治：地方议员、乡镇市长、地方政治人物、公开支持关系；
3. 地方组织：学术资料确认的地方派系、农渔会、地方社团、历史政治组织；
4. 历史研究：地方志、学术论文、县市政治研究、乡镇个案；
5. 当前验证：对历史人物、派系和组织寻找近 5—10 年资料，确认政治连续性。

当宿主 Agent 执行 Web/Search 时，应将结果按 `schemas/retrieval_lead.yaml` 送入 `HostRetrievalBackend`。检索结果默认只进入 `cache/retrieval/`：
- 缺失或非法 `source_grade` 自动降为 E；
- `verification_status=verified` 仅表示宿主已核对来源，不代表已成为知识库事实；
- 不得自动写入 `knowledge/`；
- A/B 级或两个独立 C 级来源仍需完成结构化 claim/relationship、时间范围与当前有效性校验。

停止条件见 `rules/local_knowledge_rules.yaml`。无法满足时，输出 `unknown` 或“现有公开资料不足以确定原因”。

### STEP 8A：知识晋升与地方知识 Builder

retrieval lead 不得直接进入长期知识。只有宿主已经完成结构化 proposal 后，才允许调用 `KnowledgePromotionBuilder`。

固定流程：

`retrieval lead → structured proposal → evidence gate → contradiction/time/freshness gate → promotion receipt → knowledge/ → county package`

硬规则：

- Builder 不得从摘要自动推断 subject、object、relationship_type、current_status 或因果关系；
- proposal 必须填写 `contradiction_check_completed=true` 和非空 `scope_boundary`；
- A/B 级 verified 来源可满足基础晋升门槛；
- C 级必须至少两个 verified 且 `independence_key` 不同的独立来源；
- D/E 级不得晋升；
- 已验证 A/B/C 相反证据存在时必须转 `requires_review`；
- 当前地方关系、候选人档案、当前议题必须同时通过 freshness；
- 每次晋升生成可审计 receipt，并记录 record/evidence SHA-256；
- 重复晋升同一记录必须幂等；同 ID 内容冲突且无法证明新记录更新时转 `requires_review`。

晋升后的权威记录写入：

- `knowledge/historical/<county>/claims.jsonl`
- `knowledge/local/<county>/relationships.jsonl`
- `knowledge/local/<county>/candidates.jsonl`
- `knowledge/local/<county>/issues.jsonl`

地方知识 Builder 生成：

- `knowledge/counties/<county>/package_manifest.yaml`
- `knowledge/counties/<county>/evidence_index.jsonl`
- `knowledge/counties/<county>/unresolved_questions.jsonl`
- `knowledge/counties/<county>/political_ecology.md`

这些 generated files 只是索引，不得产生新的政治事实或因果判断。分析仍以原始 `knowledge/historical/` 与 `knowledge/local/` 记录为权威来源。

### STEP 8B：Live Campaign State

V1.4 在地方知识与民调最终解释前建立选战进行时快照：

1. 明确 `as_of`；
2. 对公开新闻执行“发现 → 正文读取 → 证据摘录 → 实体识别 → 跨来源聚类 → Campaign Event”；
3. 汇总最近 30／14／7 日已验证竞选事件与结构化媒体事件，并严格区分 verified、corroborated_media 与 single_source_media；
4. 比较候选人格局、结构化事件、retrieval lead 与上一快照；
5. 只在 pollster、commissioner、method、sample_frame、question_wording 一致时计算 same-series poll delta；
6. 当前候选人变化、已验证事件、corroborated_media 事件、组织／支持变化、政党合作、重大议题、争议、司法事件或同源民调变化均可触发 `campaign_change_trigger`；
7. trigger 只产生研究问题，不代表任何候选人受益、受损、领先或更可能当选；
8. lead_only 与 single_source_media 不得作为已确认事实；corroborated_media 也必须明确写明仍待官方资料、当事人原始声明或更高等级来源确认。

### STEP 9：民调校准

民调模块永远放在结构分析之后。使用前按 `rules/poll_rules.yaml` 检查：

`pollster`、`commissioner`、`method`、`sample_size`、`sample_frame`、`field_start`、`field_end`、`moe`、`moe_applicable`、`undecided`。

民调只用于检验当前状态是否偏离历史结构，不得替代历史结构。

V1.4 允许对“同一调查系列”计算相邻波次点估计变化，但必须同时满足调查机构、委托方、方法、样本框与题型可比；该变化只能作为当前选战变化信号，不得自动写成统计显著、胜负逆转或当选概率变化。

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

县市分析采用“当前选战优先”结构。核心判断 150—250 字，必须标明 `as_of`。

先读取 Analysis Context 的 `campaign_state_status`。只有 `current` 才可对当前选战作完整判断；`partial_current_data` 必须逐项限定已核资料范围；`insufficient_current_data` 只写历史结构与当前资料缺口，不得以历史票型充当现时选情。检索结果即使自称 verified 仍按 lead_only 处理，需经独立核验后进入 L4 缓存。相反事件进入 requires_review，不得择一当作事实。所有具体政治事实必须来自 Analysis Context 所列证据。

## 【县市名称】

### 核心判断

第一句直接回答截至当前选战处于什么状态，以及最近变化是否触及原有结构；不得从历史沿革或单一民调数字开始。

### 一、截至目前选战打到哪里

说明当前候选人格局、提名／登记状态，以及最近 7／14／30 日可验证的重要变化。

### 二、最近哪些变化真正值得关注

比较上一 Campaign State Snapshot，区分已验证事件、竞选阵营主张、未验证线索与同源民调变化。

### 三、这些变化相对历史结构意味着什么

调用最近至少三届同类选举、总统与立委资料作为参照；历史只用于解释当前，不得把过去直接外推为今天。

### 四、变化主要发生在哪里

识别 3—8 个关键乡镇市区；若当前没有区级动态资料，应明确说明，不能用历史票型伪装成当前变化。

### 五、候选人及地方组织如何变化

分析长期经营、竞选组织、公开支持、现任交棒、候选人残差及其近期变化。

### 六、第三势力和中间票如何重新组合

不得机械加票，不得预设第三势力票流向。

### 七、当前民调是否验证选战变化

优先观察同一调查系列；不同机构、方法或题型只能并列说明，不得直接连成趋势。

### 八、当前竞争结构由哪些因素共同形成

归纳 2—4 个经证据支持的结构因素，并明确哪些判断仍为 unknown。

### 九、下一阶段观察什么

只列可公开验证变量，不得写胜率、必胜票数、候选人排名或政治推荐。

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

## 十、版本范围

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
- 统一 `AnalysisPipeline` 编排 readiness → loader → matrix → metrics → knowledge → freshness → context；
- CLI `readiness`、`build-matrix`、`metrics`、`context`、`run`。

V1.2 真实历史数据源增加：

- 内置中选会 `cec_open_data` A 级 Adapter；
- 官方来源：政府资料开放平台「选举资料库（含选举区资料）」及中选会 `votedata.zip`；
- 当前正式支持：2014/2018/2022 县市长、2016/2020/2024 总统、2016/2020/2024 区域立委；
- 最低支持乡镇市区层级，并保留官方原始 ZIP SHA-256、成员路径和取数时间；
- 首次 ONLINE 下载官方 ZIP，之后本地缓存复用；OFFLINE 不下载；
- 从已验证中选会结果同步最小行政区资料，供 Readiness Gate 使用；
- 2026 县市长民调可通过已注册 PollSource 补充；首个内置来源为 TVBS 民调中心原始 PDF，来源等级 C，仅用于结构校准；
- 民调必须保留调查方法、样本框、抽样、加权、调查期、误差、未决定比例与原始报告引用；关键方法字段缺失则拒绝入库；
- 民调 freshness 依据调查结束/发布日期，不依据最近抓取时间；重新下载旧民调不得使其变成当前民调；旧民调必须标记 stale，且不得作为当前状态校准；
- 增加宿主 Web 检索 inbox 桥；搜索结果保持 lead-only，不自动晋升为长期地方政治知识；
- 第三方整理资料不得替代上述官方历史事实源，除非官方源明确缺失且按证据规则降级处理。

V1.3 知识晋升与地方知识 Builder 增加：

- `KnowledgePromotionBuilder`：确定性执行 evidence、time、freshness、contradiction 与字段门禁；
- `knowledge-ingest` CLI：将宿主 Web/Search JSON/JSONL 只导入 retrieval staging；
- `knowledge-promote` CLI：从 JSON/JSONL proposal inbox 晋升知识，支持 dry-run；
- `knowledge-build` CLI：重建县市知识 package；
- retrieval lead 与 L1-L5 长期知识之间增加 pre-knowledge staging，不新增第六知识层；
- 支持 `historical_claim / local_relationship / candidate_profile / current_issue` 四类晋升；
- C 级来源必须显式提供两个不同 `independence_key`；
- 每次决策保留 promotion receipt 与 SHA-256 审计哈希；
- 同 ID 幂等写入与冲突保护；
- county package 自动生成证据索引、未解决问题与政治生态索引；生成文件只做索引，不创造事实。

后续阶段再增加村里／投票所空间分析、Neighbor Divergence 自动化、更多经过人工结构化的地方政治关系与多县市横向比较。

V1.4 Live Campaign State 增加：

- 新增 `runtime/campaign_state.py`，生成带 `as_of` 的当前选战快照；
- 固定观察最近 7／14／30 日竞选变化；
- 保存上一快照并计算候选人格局、新事件、新民调差异；
- 当前选战变化可独立触发地方知识检索；
- ONLINE 模式主动向宿主 RetrievalBackend 提出当前选战检索问题；
- 宿主结果默认保持 lead_only，未验证不得写成事实；
- 同一调查系列允许计算 same-series poll delta，不同系列仍禁止拼接；
- 默认写作顺序改为“当前态势 → 最近变化 → 历史参照 → 地方结构 → 民调校准”；
- Campaign State Snapshot 只是 L4/L5 的动态时间索引，不新增第六知识层。

V1.4 22 县市知识生产增加：

- `CountyKnowledgeProduction` 覆盖 22 县市与十类统一研究主题；
- CLI 支持全量、指定县市、增量、dry-run、状态检查、失败恢复与输入哈希幂等；
- 可调用既有 GLM-5.3 Flash + Tavily 自动研究，但输出保持 retrieval lead，不自动生成 proposal 或晋升事实；
- county package 增加 `research_questions.jsonl`、`entity_relation_index.jsonl`、`county_template.yaml` 与 `production_state.yaml`；
- `KnowledgeLoader` 读取 package，`AnalysisPipeline` 生成 evidence-bounded `event_importance_signals`；
- event importance 只做实体与已晋升知识匹配，不产生因果判断、候选人评分、胜负预测或政治建议；
- 高雄市、台南市、新北市提供首批官方 A 级行政/空间背景 seed；其余不足主题明确保留 unresolved。

`examples/yilan/` 只作为测试用例，不得成为 Skill 运行依赖。
