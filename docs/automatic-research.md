# 自动 Web Search 研究

本模块补齐“研究问题待人工补查”的执行环节。历史票型、候选人资料、近期竞选事件、地方关系研究和同系列民调校准仍由原分析主线处理。模型补查不自动提升长期知识等级，也不生成胜负概率。

## 配置与启动

在部署机器本地 `.env` 或进程环境中设置（不要提交密钥）：

```dotenv
OGASAWARA_BOT_MODE=online
OGASAWARA_BOT_RETRIEVAL=local
OGASAWARA_AUTO_RESEARCH=true
OPENCODE_GO_API_KEY=填入自己的密钥
TAVILY_API_KEY=填入自己的密钥
RESEARCH_LLM_MODEL=glm-5.3-flash
RESEARCH_LLM_BASE_URL=https://opencode.ai/zen/go/v1
RESEARCH_FOREGROUND_SECONDS=60
RESEARCH_DAILY_SEARCHES=120
RESEARCH_DAILY_TOKEN_BUDGET=500000
```

Go 调用 `/chat/completions`，使用真实应用 User-Agent 和每任务稳定的 `x-opencode-session`。采用经校验的 JSON 计划，由本程序执行工具；不依赖 Go 原生 `web_search` 或客户端内置 MCP。Tavily 独立认证与计费，使用 basic 搜索，不请求供应商生成的答案。Go 的非编程研究用途需由部署者确认适用。本项目不伪装编码流量。

机器人启动方式不变：

```bash
bash scripts/run_feishu_bot.sh
```

建议同时常驻研究工作进程，负责程序重启后的恢复、前台超时任务及延迟重试；它与原新闻采集器可以同时运行：

```bash
bash scripts/run_research_worker.sh
# 或
python -m runtime.auto_research
python -m runtime.auto_research --status
python -m runtime.auto_research --once
```

Windows 可在项目目录使用上述 Python 命令；先安装 `requirements.txt`。研究 CLI 与机器人一样读取项目 `.env`，已有环境变量优先。CLI `--status` 不调用外部 API、不打印密钥。缺少密钥不会影响原有本地新闻分析，但报告明确显示配置问题。

本次 Go 接口用于自动研究。原有 `OPENAI_*` 路由/报告设置独立；不配置 OpenAI 时，确定性飞书摘要同样展示自动补查结果、正文引用和未解决问题。

## 执行及证据边界

在线、未指定 `as_of` 的分析先生成研究问题，再创建县市/年份/选举类型/候选人/问题范围对应的任务。同一范围复用30分钟内完成的研究，不因本地已有几篇新闻就跳过覆盖检查。前台最多等60秒；超时先返回原有材料和任务状态，工作进程继续处理。重新发起县市分析读取完成结果，不额外自动群发消息。

任务最多两轮、六个查询、二十次正文候选读取，工作时间上限300秒（正在进行的有界 HTTP 读取可能略有收尾时间）。初轮最多四个查询，补搜最多两个。近期动态使用近30日检索，历史背景不加30日下限。实际读取原文后再次检查日期与地域；缺失日期的正文可作带日期未知标记的研究线索，不能形成新鲜竞选事件。

`config/research_sources.yaml` 管理媒体身份，与 `config/news_sources.yaml` 的 RSS 启用状态独立。未知站点可读取公开 HTTPS 正文，但保持 other/E。URL、DNS、robots、大小及重新导向约束沿用正文读取器；不绕过付费墙或登录，不接受搜索摘要替代正文。

每个结论的全部引用都必须对应已抓取正文中的连续原文，否则整条结论丢弃。`body_grounded_unverified` 只证明引文可追溯，不证明模型推论正确或报道属实。模型摘要应按“该来源报道”使用；未知网站、阵营主张、单一来源、转载和冲突不能自动升级成已核实事实。长期知识仍走已有晋级门禁。

预研究阶段不保存 Campaign Snapshot，完成补查后重新冻结精确到微秒的时间截点，并重建本次分析，避免新正文被旧截点排除。显式 `as_of` 与离线分析不调用研究接口。旧历史研究任务也不会用今日搜索填充当时已知资料。

## 持久化、限额和恢复

数据仍保存在 `cache/news/news.sqlite3`。仅新增 `research_runs`、`research_calls` 两张表和一个索引，不删除或重写现有表。任务复用现有 `jobs`；正文复用 `articles/article_versions`。搜索返回、正文摘录、引用、错误码、实际 token 数保留在研究检查点中；密钥和认证头不入库。

同一数据库同时只允许一个研究任务执行，避免机器人和工作进程重复消费。每次外部操作前续租；失去租约的工作者不能提交研究检查点。完成过的查询和正文在重试中复用。进程恰在外部请求返回、检查点落盘之前退出时，该次请求可能重发；调用预算仍保留，不能承诺外部 API 恰好一次。

429、网络错误、5xx 按持久化退避重试，遵守 Retry-After，最多三次任务尝试；认证错误不循环重试。失败或不确定是否计费的调用也占预算。每天按 UTC 零点重置搜索次数及模型 token 预留上限，跨进程共享。Token 预留按输入 UTF-8 字节数加最大输出保守估算，实际用量另列；额度不是供应商美元账单的精确替代。不自动换模型或开启供应商额外付费余额。

关闭 `OGASAWARA_AUTO_RESEARCH` 并停止工作进程即可回到本地新闻模式；保留现有数据库，无需删除表。已有研究取得的正文仍可按原有证据规则查询。

## 验证

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

自动测试通过模拟 API 覆盖模型协议、真实搜索执行路径、跨固定媒体正文导入、引用伪造、来源等级、离线与历史隔离、限流、预算、租约、断点恢复、快照时间及飞书摘要。真实供应商联调需在有两项密钥且可联网的部署进程执行；不会把模拟测试写成真实联网验收。
