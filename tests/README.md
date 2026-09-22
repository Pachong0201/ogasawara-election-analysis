# 测试

本目录包含 Skill V1.0 契约测试与 V1.1/V1.2 Data & Runtime 行为测试。

## 运行

```bash
python3 -m unittest discover -s tests -v
```

测试依赖以根目录 `requirements.txt` 为准；涉及官方 PDF 的 Adapter 使用 `pdfplumber`。

## 测试内容

### V1.0 契约

- `test_skill_contract.py`
- 推荐文件结构、YAML 可解析性、核心分析路径、14 条硬性禁止规则、POLL-01 至 POLL-08、输出模板、宜兰测试用例与运行依赖隔离。

### V1.1 / V1.2 Data & Runtime

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
