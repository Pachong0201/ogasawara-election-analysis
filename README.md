# 小笠原选举分析 Skill

`ogasawara-election-analysis` 是一个面向台湾选举的结构化分析 Skill。它借鉴小笠原欣幸公开展示的选举研究方法，以历史结构为基准，同时追踪选战进行时的最新变化，重点回答“截至今天，这场选战打成什么结构、最近发生了什么变化、这些变化是否触及历史结构”，而不是预测谁当选。

## 核心原则

> 先建立历史正常状态，再寻找异常；先发现异常，再解释异常。

分析路径固定为：

`历史基准 → 当前候选人格局 → Campaign State Snapshot(as_of) → 最近30／14／7日变化 → 历史／当前双触发地方知识检索 → 同源民调校准 → 当前竞争结构`

禁止把“最新民调 → 直接判断当前选情”当作主要路径，也禁止只用历史票型解释本轮选战而忽略最新变化。

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
│  ├─ knowledge_promotion.yaml
│  ├─ data_sources.yaml
│  ├─ freshness.yaml
│  ├─ campaign_state.yaml
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
│  ├─ historical_claim.yaml
│  ├─ local_relationship.yaml
│  ├─ retrieval_lead.yaml
│  ├─ campaign_event.yaml
│  ├─ knowledge_proposal.yaml
│  └─ knowledge_promotion_receipt.yaml
├─ methods/
│  ├─ electoral_swing.md
│  ├─ split_ticket.md
│  ├─ candidate_residual.md
│  ├─ spatial_divergence.md
│  ├─ incumbent_transfer.md
│  └─ third_force.md
├─ runtime/             # V1.4 数据、运行、知识晋升与Live Campaign State
│  ├─ cec_open_data.py   # 中选会官方 votedata.zip Adapter
│  ├─ cec_current_candidates.py # 2026候选人登记名册 Adapter
│  ├─ tvbs_poll_center.py # TVBS民调中心原始PDF Adapter
│  ├─ data_readiness.py
│  ├─ election_loader.py
│  ├─ election_normalizer.py
│  ├─ matrix_builder.py
│  ├─ metrics.py
│  ├─ knowledge_loader.py
│  ├─ knowledge_builder.py # V1.3知识晋升与县市package Builder
│  ├─ campaign_state.py # V1.4选战快照、7/14/30日变化与同源民调delta
│  ├─ campaign_events.py # 唯一L4事件标准化、去重及冲突审计入口
│  ├─ campaign_delta.py # 连续快照的可观察变化
│  ├─ campaign_event.py # 正文证据摘录、实体解析、跨来源聚类与Campaign Event
│  ├─ gdelt_retrieval.py # 近期新闻发现与正文读取编排
│  ├─ article_body.py # 受控公开网页正文读取
│  ├─ host_retrieval.py  # 宿主Web检索JSON/JSONL桥
│  ├─ freshness.py
│  ├─ analysis_context.py
│  ├─ pipeline.py
│  └─ cli.py
├─ data/                # 稳定历史选举事实与行政区版本
├─ knowledge/           # 最小充分地方知识缓存
├─ cache/               # 动态资料缓存
├─ references/
│  └─ ogasawara/
├─ examples/
│  ├─ yilan/            # 只作为测试用例，不是运行依赖
│  └─ knowledge_promotion/ # V1.3合成 lead/proposal 示例
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

### V1.4 Live Campaign State

V1.4 解决“历史结构很强、当前战况很弱”的问题。完整分析在历史数据之外，必须生成一个可审计的当前选战快照：

```text
as_of
→ 当前候选人格局
→ 公开新闻正文解析为 Campaign Event
→ 最近30／14／7日竞选事件
→ 与上一Campaign State Snapshot比较
→ 同一调查系列跨期变化
→ campaign_change_trigger
→ 当前地方政治与组织检索
→ 历史结构对照
→ 当前竞争结构
```

核心规则：

- Snapshot 明确记录 `as_of`，不得把不同日期资料混成“当前”；
- `current` 要求新鲜可核的候选人及近期事件；仅有一类资料为 `partial_current_data`。离线、仅有 lead_only 或全部过期为 `insufficient_current_data`，仍可输出历史结构；
- 最近 7／14／30 日分别承担近端、活跃变化与背景窗口；
- 事件由 `campaign_events.py` 统一归并，保留各来源证据；相反主张进入 `requires_review`，不得单独触发；
- 快照按县市、选举类型及年份存档；同一 `as_of` 重跑保留两个版本，比较始终先读取上一版本；
- 当前候选人变化、公开支持／组织变化、政党合作、重大议题、争议、司法事件等可独立触发地方知识检索；
- 不再要求必须先出现历史票型异常；
- ONLINE 模式会通过宿主 `RetrievalBackend` 主动提出当前选战检索问题；
- 宿主检索结果默认 `lead_only`，未经验证不得当作事实；
- 只有同一 `pollster / commissioner / method / sample_frame / question_wording` 的调查才计算 same-series delta；已披露的加权与抽样口径也必须一致；
- same-series delta 只描述点估计变化，不代表胜负趋势，也不自行宣告统计显著；
- Snapshot 只存入 `cache/campaign_state/`，属于 L4/L5 动态索引，不是新的知识层。

### V1.3 数据、运行与知识晋升层

```text
用户任务
→ Data Readiness Check
→ 本地历史选举数据
→ 缺失且 ONLINE：ElectionLoader 优先调用中选会官方 cec_open_data Adapter
→ 首次下载 votedata.zip，随后复用本地 cache/raw/cec/
→ 标准化、校验、同步最小行政区资料、持久化
→ Matrix Builder
→ Metrics
→ 地方知识检索
→ structured proposal（仅宿主显式提供时）
→ KnowledgePromotionBuilder 门禁与 receipt
→ knowledge/ + county package
→ Freshness
→ Campaign State Snapshot（as_of + 7/14/30日变化）
→ Analysis Context
→ AnalysisPipeline 统一编排
→ 按 V1.0 输出
```

常用命令：

```bash
python -m runtime.cli readiness --county "宜兰县" --year 2026 --type county_mayor
python -m runtime.cli build-matrix --county "宜兰县" --year 2026 --type county_mayor
python -m runtime.cli context --county "宜兰县" --year 2026 --type county_mayor --write-manifest
python -m runtime.cli run --county "宜兰县" --year 2026 --type county_mayor --mode offline --as-of 2026-09-23 --write-manifest
python -m runtime.cli readiness --county "宜兰县" --year 2026 --type county_mayor --mode online
python -m runtime.cli run --county "宜兰县" --year 2026 --type county_mayor --mode online \
  --retrieval-inbox cache/retrieval/inbox/yilan.jsonl --write-manifest
```

`run --as-of` 以指定日期评价候选人、事件及民调新鲜度；ONLINE 查询结果仍是线索，需核验并写入本项目的 L4 动态缓存后才可作为当前事件。没有宿主 RetrievalBackend 或可信当前事件时，报告必须显示当前资料不足。CI 在 PR 上运行全量 `python -m pytest -q`。


### V1.2 官方历史数据源

历史稳定选举事实优先使用中央选举委员会官方开放资料：

- 数据集：政府资料开放平台「选举资料库（含选举区资料）」；
- 原始下载：`https://data.cec.gov.tw/選舉資料庫/votedata.zip`；
- 来源等级：A；
- 当前支持：2014/2018/2022 县市长、2016/2020/2024 总统、2016/2020/2024 区域立委；
- 当前最低粒度：`township_district`；
- 2016 特殊 `_P1/_T1` 文件后缀由 Adapter 自动发现；
- ZIP 文件名按 CP950/Big5 元数据处理；
- 原始 ZIP 只放运行时缓存，`.gitignore` 排除，不提交仓库；
- 每条持久化记录保留官方来源、ZIP SHA-256、原始成员路径、取数与验证时间。

ONLINE 首次缺历史资料时会下载官方 ZIP；之后直接复用本地缓存。OFFLINE 不发起网络请求。第三方整理数据不得替代官方历史事实源，除非官方资料明确缺失且按证据等级规则降级。

### V1.2 民调真实来源

民调层与历史选举事实层分开管理。首个内置真实来源为 TVBS 民调中心：

- 索引：`https://www.tvbs.com.tw/poll-center`；
- 只读取民调中心索引及其原始 PDF，不以新闻二次报道替代原始报告；
- 来源等级：C；用途仅为当前结构校准，不得替代中选会 A 级历史选举事实；
- 入库必须抽取 `pollster / commissioner / method / sample_size / sample_frame / sampling / weighting / field_start / field_end / publish_date / moe / undecided / question_wording`；
- 方法字段不完整时 fail closed，不写入有效民调缓存；
- 不同机构、不同方法的民调仍不得自动连成趋势；
- freshness 以 `publish_date/field_end` 为基准。旧报告今天重新下载仍然是旧报告。

`question_wording_is_verbatim=false` 表示来源只公开报告情境摘要而非问卷逐字题目，系统不得把摘要改写成“原始问卷”。


### V1.2 宿主 Web 检索桥

地方政治、学术论文、地方人物关系等资料来源高度分散，Skill 不内置通用搜索引擎，也不通过脆弱的搜索结果页抓取来假装“自动联网”。ONLINE 模式采用宿主检索桥：

1. Skill 先根据票型异常生成明确的 research question；
2. ChatGPT、Codex 或其他宿主 Agent 使用自身 Web/Search 工具检索；
3. 宿主把结果写成 JSON/JSONL，至少包含 `query / url / source_grade / summary`；
4. 通过 CLI 的 `--retrieval-inbox` 交给 `HostRetrievalBackend`；
5. Skill 只把这些结果写入 `cache/retrieval/` 作为 lead；
6. 即使宿主标记 `verification_status=verified`，也不会自动晋升为长期 `knowledge/` 事实。

示例 JSONL：

```json
{"query":"为什么 罗东镇 出现 candidate_residual 异常？","title":"地方政治研究","summary":"研究讨论该地长期地方组织与选举结构。","url":"https://example.org/source","source_id":"host_web","source_grade":"B","verification_status":"verified"}
```

若 `source_grade` 缺失或非法，运行时自动降为 E。A/B 级或两个独立 C 级来源仍须经过结构化 claim/relationship、`time_scope` 与当前有效性检查后，才能进入长期知识库。

### V1.3 知识晋升与地方知识 Builder

V1.3 将“已检索资料”和“可复用知识”彻底分开。宿主搜索结果只进入 `cache/retrieval/`；只有结构化 proposal 通过 Builder 门禁后，才写入 `knowledge/`。

晋升链：

```text
retrieval lead
→ host structured proposal
→ source / independence gate
→ contradiction check
→ time_scope / freshness gate
→ idempotence / conflict gate
→ promotion receipt
→ knowledge/historical 或 knowledge/local
→ knowledge/counties/<county>/ generated package
```

proposal 最小示例：

```json
{
  "proposal_id": "example-r1",
  "county": "宜兰县",
  "target_type": "local_relationship",
  "research_questions": ["为什么 某乡 出现 candidate_residual 异常？"],
  "evidence_lead_ids": ["lead-a", "lead-b"],
  "contradiction_check_completed": true,
  "contradictory_lead_ids": [],
  "scope_boundary": "只确认公开关系在所列时间范围内存在，不推断选民行为。",
  "target_record": {
    "relationship_id": "r1",
    "subject": "人物A",
    "object": "组织B",
    "relationship_type": "organization_membership",
    "region": "某乡",
    "time_scope": "2025-2026",
    "current_status": "active_verified",
    "last_verified_at": "2026-09-22"
  }
}
```

先把宿主 Web/Search 结果导入 staging：

```bash
python -m runtime.cli knowledge-ingest \
  --county "宜兰县" \
  --retrieval-inbox retrieval/yilan.jsonl
```

该命令只写入 `cache/retrieval/`，不会触发任何知识晋升。

晋升前预检：

```bash
python -m runtime.cli knowledge-promote \
  --county "宜兰县" \
  --proposal-inbox proposals/yilan.jsonl \
  --dry-run
```

正式晋升并自动重建县市 package：

```bash
python -m runtime.cli knowledge-promote \
  --county "宜兰县" \
  --proposal-inbox proposals/yilan.jsonl
```

单独重建 package：

```bash
python -m runtime.cli knowledge-build --county "宜兰县"
```

晋升结果只有四种：`promoted / rejected / requires_review / dry_run_pass`。存在已验证 A/B/C 级相反证据时不会自动选择一方，必须进入 `requires_review`。

县市 package 包含：

- `package_manifest.yaml`：计数、权威源文件与生成规则；
- `evidence_index.jsonl`：已晋升记录的来源、时间、research question 与 current-use 状态索引；
- `unresolved_questions.jsonl`：尚未被晋升知识覆盖的 retrieval questions；
- `political_ecology.md`：结构化知识的可读索引，明确不得新增政治事实或因果判断。


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

## 飞书选情机器人

统一整合分支为 `feature/feishu-election-bot-v1.4-integration`。机器人采用飞书长连接，将群聊自然语言请求映射到 V1.4 `AnalysisPipeline`。

```text
飞书群 @机器人
→ thread级 Conversation State
→ Intent Router
→ 小笠原 AnalysisPipeline
→ GDELT 新闻发现 + 公开正文读取
→ Campaign Event / Campaign State
→ Analysis Context
→ OpenAI Writer（可选）
→ 飞书线程回复
```

- 群聊默认仅在 @机器人 时响应，私聊直接响应；
- “分析高雄选情”执行完整 V1.4；
- “更新一下”沿用线程 Election Focus 并重新生成 Snapshot；
- 新闻正文经实体识别和跨来源聚类后形成 Campaign Event；
- single_source_media 仅作上下文，corroborated_media 仅可触发进一步研究，不等于官方核验事实；
- OpenAI API 未配置时仍可返回确定性结构化摘要；
- App Secret / API Key 仅从环境变量读取。

完整部署说明见 `docs/feishu-bot.md`。

启动：

```bash
python -m pip install -r requirements.txt
python -m bot
```
