# 测试

本目录包含 Skill V1.0 契约测试与 V1.1—V1.4 Data、Runtime、Knowledge、Campaign State 及飞书机器人行为测试。

## 运行

```bash
python3 -m unittest discover -s tests -v
```

测试依赖以根目录 `requirements.txt` 为准；涉及官方 PDF 的 Adapter 使用 `pdfplumber`。

## 测试内容

### V1.0 契约

- `test_skill_contract.py`
- 推荐文件结构、YAML 可解析性、核心分析路径、14 条硬性禁止规则、POLL-01 至 POLL-08、输出模板、宜兰测试用例与运行依赖隔离。

### V1.1 / V1.2 / V1.3 Data、Runtime & Knowledge

- `test_cec_current_candidates.py`：2026中选会县市长登记名册、登记状态语义、缓存与 Readiness 自动刷新。
- `test_cec_open_data.py`：中选会官方 ZIP 结构、2016 特殊后缀、简繁体县市名称、缓存复用及 geography 持久化。
- `test_data_readiness.py`：数据不足 INSUFFICIENT、完整数据 READY、缺立委 PARTIAL、边界不一致拒绝、ONLINE 补齐后重检。
- `test_election_loader.py`：本地优先、缺失补齐并写回、OFFLINE 不联网。
- `test_freshness.py`：历史永久资料、30 天民调过期、封闭网络调查 MOE、1996 关系时间有效性。
- `test_matrix_builder.py`：历史矩阵、跨层级矩阵、同日分裂票矩阵。
- `test_metrics.py`：Swing、Local Swing、Split Ticket、Candidate Residual、空间差异与超额集中。
- `test_analysis_context.py`：统一 Analysis Context 与 `analysis_manifest.json`。
- `test_pipeline.py`：端到端 readiness → matrix → metrics → knowledge → context 编排，以及资料不足时禁止完整分析、stale 民调不得作为当前校准。
- `test_host_retrieval.py`：宿主 Web 检索 inbox、research question 匹配、缺失等级降为 E、lead-only 不自动晋升长期知识。

- `test_knowledge_builder.py`：V1.3 proposal 晋升、证据独立性、相反证据、freshness、幂等、receipt 与 county package Builder。
- `test_cli_knowledge.py`：`knowledge-ingest` staging 边界、`knowledge-promote --dry-run`、正式晋升、`knowledge-build` 与拒绝状态退出码。
- `test_county_knowledge.py`：22 县市注册、十主题研究计划、dry-run、批量构建、恢复、幂等、自动研究 lead-only 边界、人物—组织—地区索引与事件重要性信号。


### Feishu Bot v0.1

- `test_bot_router.py`：县市识别、更新窗口、追问 focus 继承。
- `test_bot_conversation.py`：群聊线程隔离、私聊连续会话、机器人回复链映射。
- `test_bot_service.py`：@触发、完整分析、更新重跑、上下文追问复用。
- `test_bot_report_writer.py`：未配置 OpenAI API 时的确定性回退输出。
