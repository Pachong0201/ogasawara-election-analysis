# 测试

本目录包含 Skill V1.0 契约测试与 V1.1 Data & Runtime 行为测试。

## 运行

```bash
python3 -m unittest discover -s tests -v
```

测试只依赖 Python 标准库与 PyYAML。

## 测试内容

### V1.0 契约

- `test_skill_contract.py`
- 推荐文件结构、YAML 可解析性、核心分析路径、14 条硬性禁止规则、POLL-01 至 POLL-08、输出模板、宜兰测试用例与运行依赖隔离。

### V1.1 Data & Runtime

- `test_data_readiness.py`：数据不足 INSUFFICIENT、完整数据 READY、缺立委 PARTIAL、边界不一致拒绝、ONLINE 补齐后重检。
- `test_election_loader.py`：本地优先、缺失补齐并写回、OFFLINE 不联网。
- `test_freshness.py`：历史永久资料、30 天民调过期、封闭网络调查 MOE、1996 关系时间有效性。
- `test_matrix_builder.py`：历史矩阵、跨层级矩阵、同日分裂票矩阵。
- `test_metrics.py`：Swing、Local Swing、Split Ticket、Candidate Residual、空间差异与超额集中。
- `test_analysis_context.py`：统一 Analysis Context 与 `analysis_manifest.json`。
