# 飞书选情机器人 v0.1

## 目标

把 `ogasawara-election-analysis` 暴露为飞书群聊中的自然语言选情分析机器人，同时保持 Skill 的分析纪律不变：

- 飞书只负责消息入口和回复；
- Conversation Manager 负责线程级上下文；
- `AnalysisPipeline` 负责历史结构、Campaign State、民调与证据；
- LLM 只负责自然语言表达，不得绕过 Analysis Context 自行生成政治事实。

## 当前阶段

v0.1 实现最小可运行闭环：

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

本项目采用 `lark-channel-sdk`，无需为 v0.1 暴露公网 Webhook。

## 环境变量

复制 `.env.example` 中的字段到实际部署环境的 secret store：

```bash
export LARK_APP_ID="cli_xxx"
export LARK_APP_SECRET="..."
export OPENAI_API_KEY="..."        # 可选
export OPENAI_WRITER_MODEL="gpt-5.6-sol"
export OGASAWARA_BOT_MODE="online"
export FEISHU_REQUIRE_MENTION="true"
```

注意：

- ChatGPT Plus 订阅不能替代 OpenAI API Key；
- 不要把真实密钥写入 `.env.example`、README 或 Git；
- 若未配置 `OPENAI_API_KEY`，机器人仍能运行，只是使用结构化模板回答。

## 启动

```bash
python -m pip install -r requirements.txt
python -m bot
```

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

v0.1 尚未完成：

1. 通用实时 Web Search RetrievalBackend。
   现有 Skill 可继续使用中选会、候选人登记与已注册民调 Adapter，但离开 ChatGPT 宿主后，通用新闻/网页实时检索仍需单独接入。
2. 飞书交互卡片按钮。
3. 任务级缓存锁和同县市并发合并。
4. “简单历史数字查询”直查数据库的 Quick QA 快路径。
5. LLM Intent Router；目前使用可审计的规则路由。
6. 生产级可观测性、限流、权限白名单与管理员命令。

这些属于 v0.2 / Phase 2，不应通过让 LLM 自行搜索绕过。

## 测试

```bash
python -m unittest discover -s tests -v
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
- 无 OpenAI API 时的回退输出。
