# 知识深度缺口表（按县市 × 主题）

本表由 `scripts/knowledge_gap_report.py` 生成，数据时点 2026-09-27。未解决只表示尚无通过 `KnowledgePromotionBuilder` 门禁的证据，不代表地方没有相关事实。

## 主题汇总

| 主题 | 已有晋升证据县市 | 未解决县市 | 证据索引记录 |
|---|---:|---:|---:|
| historical_political_structure | 0 | 22 | 0 |
| people | 0 | 22 | 0 |
| organizations | 3 | 19 | 12 |
| political_networks | 2 | 20 | 2 |
| electoral_geography | 0 | 22 | 0 |
| civil_associations | 0 | 22 | 0 |
| farmers_fishermen_associations | 0 | 22 | 0 |
| religious_organizations | 0 | 22 | 0 |
| key_issues | 0 | 22 | 5 |
| demographics_industry | 22 | 0 | 44 |

总计：220 个研究问题，196 个未解决。

## 22 县市明细

| 县市 | 未解决问题 | 历史主张 | 地方关系 | 2026 市长档案 | 2026 议员档案 | 当前议题 | 晋升回执 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 南投縣 | 10 | 2 | 4 | 2 | 61 | 1 | 76 |
| 台中市 | 8 | 2 | 13 | 3 | 102 | 1 | 140 |
| 台北市 | 9 | 2 | 9 | 6 | 98 | 1 | 138 |
| 台南市 | 7 | 4 | 9 | 4 | 88 | 1 | 122 |
| 台東縣 | 10 | 2 | 3 | 4 | 56 | 1 | 72 |
| 嘉義市 | 9 | 2 | 2 | 5 | 37 | 1 | 54 |
| 嘉義縣 | 9 | 2 | 3 | 2 | 53 | 1 | 67 |
| 基隆市 | 9 | 2 | 2 | 3 | 60 | 1 | 73 |
| 宜蘭縣 | 11 | 2 | 3 | 5 | 59 | 1 | 77 |
| 屏東縣 | 9 | 2 | 3 | 2 | 87 | 1 | 101 |
| 彰化縣 | 10 | 2 | 6 | 4 | 86 | 1 | 111 |
| 新北市 | 7 | 3 | 15 | 3 | 112 | 1 | 161 |
| 新竹市 | 9 | 2 | 2 | 3 | 56 | 1 | 69 |
| 新竹縣 | 9 | 2 | 3 | 3 | 68 | 1 | 84 |
| 桃園市 | 8 | 2 | 7 | 2 | 109 | 2 | 136 |
| 澎湖縣 | 9 | 2 | 5 | 6 | 36 | 1 | 58 |
| 花蓮縣 | 11 | 2 | 4 | 4 | 55 | 1 | 72 |
| 苗栗縣 | 9 | 2 | 3 | 2 | 56 | 1 | 71 |
| 連江縣 | 9 | 2 | 2 | 2 | 14 | 1 | 25 |
| 金門縣 | 9 | 2 | 3 | 7 | 35 | 1 | 57 |
| 雲林縣 | 9 | 2 | 3 | 4 | 73 | 1 | 91 |
| 高雄市 | 6 | 4 | 11 | 5 | 101 | 1 | 144 |

## 三个深度核验样板

### 台北市

- 未解决问题：9／10；已晋升证据记录：116
- 历史主张 2 条；地方关系 9 条；候选人档案 104 条；当前议题 1 条
- 晋升回执：promoted 138、rejected 0、requires_review 0、反证检查 138
- 学术线索（正文未读取，不能晋升）：
  - academic-taipei-shilin-beitou-election-2011（B，2011）：铭传大学硕士论文《政党的地方选战策略-以2010年台北市第11届士林北投区议员选举为例》
- 待补主题：historical_political_structure、people、organizations、political_networks、electoral_geography、civil_associations、farmers_fishermen_associations、religious_organizations、key_issues

### 宜蘭縣

- 未解决问题：11／10；已晋升证据记录：70
- 历史主张 2 条；地方关系 3 条；候选人档案 64 条；当前议题 1 条
- 晋升回执：promoted 77、rejected 0、requires_review 0、反证检查 77
- 学术线索（正文未读取，不能晋升）：
  - academic-yilan-factional-politics-1996（B，1996）：政大硕士论文《宜兰县派系政治之研究》
- 待补主题：historical_political_structure、people、organizations、political_networks、electoral_geography、civil_associations、farmers_fishermen_associations、religious_organizations、key_issues

### 高雄市

- 未解决问题：6／10；已晋升证据记录：122
- 历史主张 4 条；地方关系 11 条；候选人档案 106 条；当前议题 1 条
- 晋升回执：promoted 143、rejected 1、requires_review 0、反证检查 142
- 学术线索（正文未读取，不能晋升）：
  - 本县市未配置 academic_records 线索；historical/local 中已有时间限定的学术 B 级记录须按正文核验。
- 待补主题：historical_political_structure、people、electoral_geography、civil_associations、farmers_fishermen_associations、religious_organizations

## 外部限制

- 本轮正文读取状态：not_read_in_this_run: local .gov.tw / NDLTD network unavailable and auto-research credentials absent
- 自动研究：{"enabled": false, "configuration_error": "missing_research_credentials", "pending_jobs_not_counted_as_success": true, "backoff": "ProviderError.retryable with attempts<3 and retry_after; pending/retry_pending never counted as success"}
