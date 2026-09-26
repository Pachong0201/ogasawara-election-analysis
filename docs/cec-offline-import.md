# 中选会离线导入与空间矩阵

## 目的

中选会站点发生 TLS 中断时，不再让网络问题阻塞官方历史选举数据底座。下载动作与解析动作彻底分离：本模块**不会联网**，只读取操作者提供的官方本地文件，并记录 SHA256、文件大小、ZIP CRC、CSV 原始列和导入时间。

## 输入

支持两类官方文件，可单独或同时导入：

1. 中选会「选举资料库（含选举区资料）」`votedata.zip`；
2. 中选会「第11届立法委员选举区范围」CSV。

原始大文件仍不提交 Git；导入器会按 SHA256 将原始 ZIP 复制到本地 `cache/raw/cec/imports/<sha256>/`，并把可追溯清单写入 `data/manifests/cec_artifacts/<sha256>.json`。导入后的结构化结果写入 `data/`，本次运行总清单写入 `data/manifests/cec_offline_import_latest.json`。

## 用法

```bash
# 同时导入 ZIP 与第11届立委选区范围，覆盖22县市
python -m runtime.cec_offline_import \
  --archive D:/cec/votedata.zip \
  --boundaries D:/cec/立法委员选举区范围.csv \
  --archive-publication-date 2026-09-26 \
  --strict

# 只处理三个县市的 ZIP
python -m runtime.cec_offline_import \
  --archive D:/cec/votedata.zip \
  --counties "高雄市,台南市,新北市"

# 只导入选区边界 CSV
python -m runtime.cec_offline_import \
  --boundaries D:/cec/立法委员选举区范围.csv
```

## 产物

- `data/elections/<type>/<year>/<county>.jsonl`：沿用现有 CEC Adapter 的行级官方选举记录；
- `data/geography/administrative_areas/cec_<county>.yaml`：现有 Loader 从选举记录同步的乡镇市区版本记录；
- `data/geography/electoral_districts/cec_legislator_term11/<county>.jsonl`：第11届立委选区范围，`boundary_version` 由原始 CSV SHA256 派生；
- `data/matrices/cec/<county>.json`：县市历届 `region × election_type × year × candidate` 空间矩阵，以及各选举类型历史矩阵；
- `data/manifests/cec_artifacts/<sha256>.json`：原始 ZIP 的 URL、文件名、发布日期（如提供）、SHA256、大小与本地归档路径；
- `data/manifests/cec_offline_import_latest.json`：本次导入的来源、哈希、成功/缺失切片与矩阵文件清单。

## 失败关闭原则

CSV 缺少「选举区／选举区范围」字段、ZIP 为空/超限/CRC 异常、Adapter 无法解析某届某县市时，不猜测、不手抄、不生成替代数据。`--strict` 下任何请求切片缺失或无效均返回非零退出码，便于在本地任务或 CI 中设置门禁。


## 刷新语义

每次显式提供 ZIP 都会重新解析该 ZIP，不会因为 `data/elections/` 已存在旧 JSONL 而跳过。记录中的 `boundary_version` 由选举类型、年份与原始 ZIP SHA256 派生；因此跨届或原始档案变更时，空间矩阵可以识别版本差异并要求人工检查边界可比性。
