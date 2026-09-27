# 知识库下一阶段建设验收报告

日期：2026-09-27　分支：`feature/feishu-election-bot-v1.4-integration`
范围：知识库数据完整性、运行时数据接入、官方社会背景、地方知识深度。

## 0. 边界

- 保留未提交的机器人修复 `bot/feishu_app.py`、`bot/service.py`、`tests/test_bot_service.py`、`tests/test_feishu_app.py`，本阶段未改动、未提交。
- 未重启飞书服务、未更换模型、未修改任何密钥。
- 目录、搜索摘要、模型输出一律不作为已核实知识；本轮没有在缺少正文证据的情况下晋升任何新的政治主张。
- 农渔会会员数/社团名册只按组织基础数据导入，不推断支持、动员或投票行为。

## 1. P0-1：农会、渔会数据相互覆盖

### 根因

`CountyContextCatalog.import_file` 以 `kind` 为文件名写入 `data/context/<county>/<kind>.jsonl`，农会与渔会共用
`farmers_fishermen_associations`，后导入的来源整文件覆盖先导入的来源。修复前 19 县只剩渔会、3 县只剩农会。

### 修复

- `import_file` 改为读取既有文件，仅替换 `source_id` 相同的记录，再确定性排序写回；其他来源记录保留。
- 同来源重复导入幂等；来源更新后旧记录不残留；某县无新记录且无其他来源时删除空文件。
- manifest 增加 `replaced_source_row_count`、`retained_row_count`、`retained_by_source`、`retained_by_county`、
  `reconciliation`（原始行数=归属行数+未映射行数）与 `unmapped_reason`。
- 原始缓存缺失、政府站点本地不可达时，通过项目自带 GitHub Actions 工作流重新下载官方资料并记录 SHA-256；
  也可用 `runtime.context_recovery` 从带 provenance 的规范化记录重建（要求行号连续、官方哈希唯一）。

### 修复前后对照

| 指标 | 修复前 | 修复后（官方重新下载） |
|---|---:|---:|
| 农会归属行数 | 273（仅 3 县） | 1,866（22 县） |
| 渔会归属行数 | 1,596（19 县） | 1,596（19 县） |
| 归属行数合计 | 1,869 | 3,462 |
| 未映射行数 | 116 | 116 |
| 农渔会并存的县市 | 0 | 19 |
| 仅有农会/仅有渔会的县市 | 3 / 19 | 3 / 0（官方数据无该县渔会行） |
| 官方原始 SHA-256（农会） | 9be5a43e… | 9be5a43e…（一致） |
| 官方原始 SHA-256（渔会） | 13589e8f… | 13589e8f…（一致） |

对账：农会 1,982 = 1,866 + 116；渔会 1,596 = 1,596 + 0。农会恢复的 1,593 行与原始官方哈希一致。

### 测试

`tests/test_county_context_catalog.py` 新增 5 个场景并全部通过：农会→渔会、渔会→农会、重复导入幂等、
来源更新删除旧行且保留其他来源、未映射行按来源替换；文件内共 8 项测试通过。

## 2. P0-2：知识库候选人与运行时接通

### 读取优先级与规则

`ElectionLoader` 新增 `load_candidate_pool` / `rebuild_current_candidate_cache`：

1. `cache/candidates/<县市>.jsonl`（官方适配器缓存）优先；
2. 缓存不可用时读取 `knowledge/local/<县市>/candidates.jsonl`（必须带 `promotion_provenance`）；
3. 运行时按目标县市、选举类型、年份、登记状态、来源等级、适用日期严格筛选，议员档案不会满足县市长检查；
4. 未知状态、退选/失格、来源不足、未晋升、验证日期晚于目标日期、过期记录全部 fail closed；
5. `python -m runtime.cli sync-candidates` 可从可用记录重建缓存（写入时保留原始 `last_verified_at`）。

### 22 县市离线矩阵（as_of 2026-09-27）

- 全部 22 县市 `READY`；`current_candidate_list` 22/22 满足且均为 `registered`；
- 21 县直接读取已晋升 `knowledge/local`，台北市同时命中重建后的 cache 与 knowledge；
- 每县市矩阵记录 `source_counts`、`rejection_reasons`、`excluded_other_election_types` 与 warnings；
- 交付：`data/manifests/readiness_county_mayor_2026_offline.json`；命令 `readiness-matrix`。

台北离线样例：6 名已登记县市长候选人，全部 `county_mayor` / 2026 / 登记状态，无议员混入，报告 `READY`。

### 测试

`tests/test_candidate_runtime.py` 5 项通过：跨类型/退选/未知状态/来源不足/未晋升/未来日期的 fail-closed、
重建缓存不混入议员且日期不被刷新、缓存为空时 readiness 读取晋升档案、议员档案不能满足县市长门槛、
台北离线样例回归。

## 3. P1：官方社会背景

| 来源 | 状态 | 覆盖 | 缺口与判定 |
|---|---|---|---|
| 2020 年龄结构 | **已修复并在 CI 验证** | 22 县 / 22 行（每县一条 age_summary） | 修复前 `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`；CI 运行 36308476297 首次下载成功，重试轮复用缓存 |
| 寺庙 | 代码已实现有界重试+缓存复用，外部仍阻断 | 0 县 | CI 与本地均 `[Errno 110] Connection timed out`；主机证书有效（2027-01 到期），属外部网络不可达，非重试参数问题 |
| 全国性社会团体 | 行级数据可用 | 21 县 / 22,101 行 | 连江无“单一县市匹配”行；1,759 行无县市名、4 行跨多县，属真实无可归属而非写入失败 |
| 农会会员数 | 行级数据可用 | 22 县 / 1,866 行 | 116 行无县市名，保留在 `_unmapped` |
| 渔会会员数 | 行级数据可用 | 19 县 / 1,596 行 | 台北、嘉义市、新竹县官方数据无渔会行，非映射失败 |

证书链修复方式：`config/certs/twca_secure_ssl_ca.pem` 为公开的 TWCA 中间 CA 证书（无私钥），本地已校验其
签名可链到 certifi 的 TWCA Global Root CA，且 `ws.dgbas.gov.tw` 叶子证书 SAN 与签名均验证通过；
`_ssl_context()` 仍保持 `CERT_REQUIRED` 与主机名校验，仅补全服务器未发送的中间证书。CI 运行
36308476297 的结果：年龄结构 22 行成功入库；寺庙在两次 CI 下载尝试与本机探测中均为连接超时，
维持 0 行并保留在 `unmapped`/失败清单中，不伪造数据。

每个来源的 `data/manifests/context_<source_id>.json` 与 `data/manifests/context_source_inventory.json` 保留：
dataset/resource URL、`time_scope`、更新时间频率、导入时间、官方 SHA-256、raw cache SHA-256 与路径、
原始/归属/未映射行数、按县归属、未映射原因、字段诊断样本、下载失败或 fallback 记录。

## 4. P1：地方知识深度

- 缺口表：`data/manifests/knowledge_gap_matrix.json` + `docs/knowledge-gap-report.md`；当前 220 个研究问题中
  196 个未解决。已晋升证据集中在人口产业（22/22 县）、组织（3 县）、政治网络（2 县）；历史政治结构、
  人物、选举地理、社团、农渔会、宗教、关键议题仍大面积未解决。
- 样板：台北、宜兰、高雄清单见 `data/manifests/knowledge_depth_samples.json`，列出主题证据数、未解决明细、
  学术线索、反证检查与晋升回执；高雄无 academic_records，但 historical 中有 4 条时间限定的 A/B 级记录。
- 学术线索一律 `promote: false`，必须读取正文、记录页码/时间范围/地理范围、完成反证检查并提交结构化
  proposal 后才能晋升；本轮因外部正文不可达，未做任何无正文晋升。
- 现有数据（复核后）：历史主张 49、地方关系 115、候选人档案 1,583（2026 市长 81 + 议员 1,502，全部登记
  且 A 级）、当前议题 23、证据索引 1,770、实体关系 115；晋升回执 promoted 1,997、rejected 2。
- 单次活动只记录当日事实，不写成长期联盟；原“任职/出席/社团存在”不得推断支持或动员。

### 自动研究可用性检查

`python -m runtime.research_preflight` 返回 `{"status": "configuration_error", "error": "missing_research_credentials"}`，
未调用外部 API；`auto_research --status` 同时显示 34 个 pending 研究任务且
`pending/retry_pending` 不计为成功。代码已有 429 退避：`ProviderError.retryable` 且 `attempts < 3`，
退避取 `base_delay * 2^(attempts-1)` 与 `retry_after` 较大者；凭据缺失时不反复重跑县市。

## 5. 回归测试

```
python3 -m pytest -q
292 passed, 251 subtests passed, 5 failed
python3 -m pytest tests/test_county_context_catalog.py tests/test_context_official_download.py tests/test_candidate_runtime.py tests/test_data_readiness.py tests/test_cec_current_candidates.py tests/test_election_loader.py -q
通过
```

5 个失败均为 `tests/test_article_body.py` 与本阶段无关的既有环境差异（trafilatura 提取行为），未由本次改动引入。

## 6. 验收五问

1. **数据是否存在？** L1 官方选举 12,756 行、选区/行政区矩阵齐全；L3 候选人档案 1,583 条；
   农渔会各 1,866/1,596 行；年龄结构 22 行已入库；寺庙行级数据仍缺（外部连接超时）。
2. **来源与证据是否合格？** 本轮涉及农渔会、社团、候选人全部 A 级官方来源；学术线索 B 级未晋升；
   C 级媒体关系仅双源可用。
3. **对指定日期是否适用？** 矩阵以 as_of 2026-09-27 筛选；候选人 `last_verified_at<=as_of`，
   登记时效为选举周期；过期/未来日期记录被拒。
4. **运行时是否实际读取？** 是。readiness 先 cache、后 `knowledge/local` 晋升档案；
   22/22 县市候选人门槛由运行时实际读取的记录满足。
5. **报告是否正确使用并披露缺口？** 矩阵记录缺口、拒绝原因、来源与 warnings；知识缺口表保留 196 个
   unresolved；自动研究 pending 未被计为成功；未在正文缺失时晋升任何主张。

## 7. 复现命令

```bash
# P0-1 覆盖审计与前后对照
python3 -m runtime.cli knowledge-coverage --all-counties > data/manifests/context_coverage_latest.json
python3 -m runtime.cli knowledge-production --all-counties --dry-run   # 不改数据

# P0-2 缓存重建与 22 县市矩阵
python3 -m runtime.cli sync-candidates --all-counties --year 2026 --type county_mayor --as-of 2026-09-27
python3 -m runtime.cli readiness-matrix --type county_mayor --year 2026 --as-of 2026-09-27 \
  --output data/manifests/readiness_county_mayor_2026_offline.json

# P1 来源清单与知识缺口
python3 scripts/knowledge_gap_report.py
python3 -m runtime.research_preflight   # 小规模可用性检查，不建立事实
```

## 8. 结论

- 已完成：农渔会覆盖修复并从官方源恢复全部农会行；候选人与运行时接通、22 县市离线 READY；
  社来源清单与缺口诊断；知识缺口表与样板；证书链补全与下载重试/缓存代码。
- 部分完成：寺庙来源仍因外部网络不可达为 0 行；地方知识深度只完成缺口表与核验清单，
  正文读取与新增晋升受外部网络/凭据限制。
- 受阻：`religion.moi.gov.tw` 在 GitHub runner 与本机均连接超时；本地环境无法访问 `*.gov.tw`
  与 NDLTD 等全文来源；自动研究无凭据，未执行付费搜索，pending 任务保持未解决。
