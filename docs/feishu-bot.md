# 飞书选情机器人 × 小笠原 Skill v1.4（统一部署版）

## 目标

把 `ogasawara-election-analysis` 暴露为飞书群聊中的自然语言选情分析机器人，同时保持 Skill 的分析纪律不变：

- 飞书只负责消息入口和回复；
- Conversation Manager 负责线程级上下文；
- `AnalysisPipeline` 负责历史结构、Campaign State、民调与证据；
- LLM 只负责自然语言表达，不得绕过 Analysis Context 自行生成政治事实。

## 当前阶段

统一整合分支 `feature/feishu-election-bot-v1.4-integration` 已将飞书机器人、实时新闻正文解析链与验收通过的 V1.4 Live Campaign State 合并。当前闭环：

```text
飞书群 @机器人
  -> lark-channel-sdk 长连接
  -> ConversationStore
  -> IntentRouter
  -> SkillService / AnalysisPipeline
  -> Analysis Context
  -> OpenAIReportWriter 或确定性回退
  -> 飞书线程回复
```

已实现：

- 群聊默认必须 @机器人；
- 私聊直接提问；
- 群聊根消息按 thread root 隔离，防止不同话题串线；
- 回复机器人消息可继续沿用上一轮 Election Focus 与 Analysis Context；
- “分析高雄选情”运行完整 V1.4 Pipeline；
- “更新一下”“最近7天有什么变化”重新运行当前分析；
- “民调怎么看”“给我看依据”优先复用当前线程 Context；
- “最新民调怎么看”会刷新 Context；
- 默认由独立多源采集器持续收集新闻，分析查询本地库；正文先经过证据摘录、候选人／地点实体识别与跨来源聚类，生成结构化 Campaign Event 后进入 Campaign State；
- 没有 OpenAI API Key 时仍可运行，返回确定性结构化摘要；
- 配置 OpenAI API Key 后，通过 Responses API 生成自然语言研判；
- 飞书 App Secret / OpenAI API Key 只从环境变量读取，不写入仓库。

## 飞书侧准备

使用自建应用：

1. 开启机器人能力。
2. 开启事件订阅的长连接 / WebSocket 模式。
3. 订阅消息接收事件。
4. 授予机器人收发消息所需权限，例如 `im:message`、`im:message:send_as_bot`。
5. 将应用安装到目标企业并把机器人加入群聊。

本项目采用 `lark-channel-sdk` 长连接，无需暴露公网 Webhook。

## 环境变量

复制 `.env.example` 中的字段到实际部署环境的 secret store：

```bash
export LARK_APP_ID="cli_xxx"
export LARK_APP_SECRET="..."
export OPENAI_API_KEY="..."        # 可选
export OPENAI_WRITER_MODEL="gpt-5.6-sol"
export OGASAWARA_BOT_MODE="online"
export OGASAWARA_BOT_RETRIEVAL="local"
export OGASAWARA_MAX_ARTICLE_FETCHES="6"
export FEISHU_REQUIRE_MENTION="true"
```

注意：

- ChatGPT Plus 订阅不能替代 OpenAI API Key；
- 不要把真实密钥写入 `.env.example`、README 或 Git；
- 若未配置 `OPENAI_API_KEY`，机器人仍能运行，只是使用结构化模板回答。
- `OGASAWARA_BOT_RETRIEVAL=disabled` 可关闭新闻检索；`OGASAWARA_BOT_MODE=offline` 也不会调用检索接口。

## 实时新闻检索

默认使用持久本地新闻库，必须独立运行 `python -m runtime.news_collector`。旧 `.env` 显式写了 `gdelt` 的部署需改成 `local`。采集配置、Windows运行方式、首次补采和错误诊断见 [多源新闻采集](news-collection.md)。

RSS/Atom、栏目列表和叶级 Sitemap 发现链接，公开正文经原有 ArticleBodyFetcher 读取后入库。机器人查询不等待新闻站点，offline 也可读取本地已采集文章。未读取正文或时间未知的文章不能生成新鲜媒体事件。GDELT仅在显式配置时启用。

## 实时事件解析链

当前在线链路固定为：

```text
Background RSS / Listing / Sitemap Discovery
→ Durable Queue / Local News Store
→ ArticleBodyFetcher
→ CampaignEventResolver
→ evidence excerpt
→ candidate / location entity resolution
→ cross-source clustering
→ single_source_media / corroborated_media
→ Campaign State 7/14/30 windows
→ Snapshot Delta
→ campaign_change_trigger
→ local knowledge research
→ Analysis Context
→ LLM Writer
```

证据边界：

- 未成功读取正文的标题不会生成 Campaign Event；
- 单一媒体正文生成 `single_source_media`，只进入上下文，不独立触发结构研究；
- 两个以上独立域名的正文若在日期、事件类型、候选人／地点及文本特征上相互匹配，可形成 `corroborated_media`；
- `corroborated_media` 在结构化事件层标记为 C 级媒体证据、`research_trigger_only`；它只表示两个以上独立媒体正文相互印证，仍不能写成 A/B 级或官方已核实事实，也不能绕过知识晋升门禁；
- 7/14/30 窗口会同时统计 verified event 与 corroborated media event；
- Snapshot Delta 分别记录新增 event、poll、retrieval lead 与 corroborated event；
- 地方知识检索会优先带入事件中识别出的行政区和候选人，减少泛化搜索；
- 最终 OpenAI Writer 不再接收整篇正文，只接收结构化事件、有限证据摘录、来源和核验状态。

## 启动

```bash
python -m pip install -r requirements.txt
python -m bot
```

另一个终端运行：

```bash
python -m runtime.news_collector
```

宿主 Python 受外部管理（无法 pip install）时，依赖已装入 `.deps/`，用启动脚本即可：

```bash
./scripts/run_feishu_bot.sh
```

脚本会注入 `PYTHONPATH=.deps` 并使用系统 Python。启动前先在仓库根目录创建 `.env`（已被 .gitignore 忽略）填写 `LARK_APP_ID` / `LARK_APP_SECRET`；`bot/config.py` 会自动加载 `.env`，真实环境变量优先于 `.env`。

启动成功后，SDK 会建立飞书长连接。

## 交互示例

### 完整分析

```text
@小笠原 分析高雄选情
```

机器人运行完整 Skill，并把当前线程 Election Focus 设为：

```json
{
  "jurisdiction": "高雄市",
  "election_type": "county_mayor",
  "target_year": 2026
}
```

### 连续追问

用户在同一线程继续：

```text
为什么凤山重要？
```

系统不重新跑完整 Pipeline，而是优先使用上一轮 `Analysis Context`。

### 更新

```text
更新一下
```

系统沿用“高雄市 / 2026 / county_mayor” Focus，重新运行 Skill，形成新的 Campaign State Snapshot。

### 民调

```text
最新民调怎么看？
```

带“最新”时重新刷新；普通“民调为什么这样解读？”则优先复用当前 Context。

## 会话隔离

ConversationKey 规则：

```text
私聊：
dm:<chat_id>

群聊/话题：
thread:<chat_id>:<root_message_id>
```

机器人发出的消息会记录到 `message_links`。用户回复机器人消息时，可回查原 ConversationKey。

状态存储：

```text
cache/bot/conversations.sqlite3
```

其中只保存：

- 当前 Election Focus；
- 上一份 Analysis Context；
- 最近用户问题；
- 消息 -> conversation 映射。

不把一个群的所有聊天记录无边界地塞给模型。

## LLM 边界

OpenAI 仅作为 Writer 使用：

```text
User
 -> Intent Router
 -> Skill
 -> Analysis Context
 -> OpenAI Responses API
 -> Feishu
```

Writer 明确禁止：

- 自主预测谁会当选；
- 给候选人排名、评分；
- 提供政治推荐或投票建议；
- 把不同机构/方法的民调拼成趋势；
- 添加 Analysis Context 中不存在的事实。

API 请求设置 `store=False`。

## 当前限制

后续工作：

1. 扩充官方原始资料 Adapter，使 corroborated_media 能进一步被 A/B/C 级来源确认；当前多媒体正文相互印证只具有 research_trigger_only 资格。
2. 飞书交互卡片按钮。
3. 任务级缓存锁和同县市并发合并。
4. “简单历史数字查询”直查数据库的 Quick QA 快路径。
5. LLM Intent Router；目前使用可审计的规则路由。
6. 生产级可观测性、限流、权限白名单与管理员命令。

这些后续工作不应通过让 LLM 自行搜索绕过。

## 测试

统一整合分支当前 GitHub Actions 验证结果：`158 passed, 146 subtests passed, 0 failed`，`Runtime online end-to-end` 同时通过。

本地可运行：

```bash
python -m pytest -q
```

机器人测试覆盖：

- 意图与县市解析；
- 7/14/30日更新窗口；
- 群聊线程隔离；
- 私聊连续状态；
- 回复机器人消息后的上下文恢复；
- 群聊未 @ 时忽略；
- 完整分析调用 Skill；
- 追问复用 Context；
- 更新重新调用 Skill；
- 公开正文提取、重复检测与证据分级；
- robots 禁止、私有地址、登录跳转和付费拒绝时停止读取；
- 无 OpenAI API 时的回退输出。
