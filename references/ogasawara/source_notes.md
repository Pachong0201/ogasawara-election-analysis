# Source Notes

此文件登记与小笠原方法相关的来源与操作化映射。新增条目时不得编造书刊、页码、链接或言论。

## V1.1 核心方法来源登记

以下条目登记的是本仓库对公开方法脉络的操作化实现，不是对外部原著的逐字复制。外部书目在未来完成图书馆或出版社核验后再行补录；在核验前不得声称某具体句子或页码来自小笠原欣幸。

```yaml
sources:
  - source_id: method_historical_baseline
    type: internal_method
    author: 本仓库方法整理
    title: 历史基准与跨届比较规则
    year: 2026
    publication: ogasawara-election-analysis
    reference:
      - rules/historical_baseline.yaml
      - methods/electoral_swing.md
    relevant_methods:
      - historical_baseline
    source_grade: internal
    verification_status: operationalization
    note: >
      先建立历史正常状态，再寻找异常；以区间与中位数描述，不写永久基本盘。

  - source_id: method_spatial_variance
    type: internal_method
    author: 本仓库方法整理
    title: 空间残差与离散程度方法
    year: 2026
    publication: ogasawara-election-analysis
    reference:
      - methods/spatial_divergence.md
      - runtime/metrics.py
    relevant_methods:
      - spatial_variance
      - neighbor_divergence
      - geographic_concentration
    source_grade: internal
    verification_status: operationalization
    note: >
      同时使用 SD、CV、IQR；得票率低时优先 SD/IQR；空间异常不得直接解释为派系。

  - source_id: method_local_politics
    type: internal_method
    author: 本仓库方法整理
    title: 最小充分地方知识与地方政治时间有效性规则
    year: 2026
    publication: ogasawara-election-analysis
    reference:
      - rules/local_knowledge_rules.yaml
      - runtime/knowledge_loader.py
      - schemas/local_relationship.yaml
    relevant_methods:
      - local_politics
      - local_election_analysis
    source_grade: internal
    verification_status: operationalization
    note: >
      历史派系、人物与组织必须记录 time_scope；历史关系不得自动外推当前。

  - source_id: method_split_ticket
    type: internal_method
    author: 本仓库方法整理
    title: 同日分裂投票与候选人残差
    year: 2026
    publication: ogasawara-election-analysis
    reference:
      - methods/split_ticket.md
      - methods/candidate_residual.md
      - runtime/metrics.py
    relevant_methods:
      - split_ticket
      - candidate_residual
    source_grade: internal
    verification_status: operationalization
    note: >
      差值统一称为候选人残差或跨票种残差，不得直接称为个人票。

  - source_id: method_local_election_analysis
    type: internal_method
    author: 本仓库方法整理
    title: 县市长、总统、立委跨层级比较规则
    year: 2026
    publication: ogasawara-election-analysis
    reference:
      - SKILL.md
      - methods/electoral_swing.md
      - runtime/matrix_builder.py
    relevant_methods:
      - local_election_analysis
      - cross_level_analysis
    source_grade: internal
    verification_status: operationalization
    note: >
      不得以总统票直接当作县市长基本盘；跨层级差异必须通过乡镇矩阵识别。

  - source_id: method_fieldwork
    type: internal_method
    author: 本仓库方法整理
    title: 地方知识检索、证据分级与停止条件
    year: 2026
    publication: ogasawara-election-analysis
    reference:
      - rules/local_knowledge_rules.yaml
      - config/source_priority.yaml
      - config/evidence_grades.yaml
    relevant_methods:
      - fieldwork
      - evidence_grading
    source_grade: internal
    verification_status: operationalization
    note: >
      异常发现后按候选人、基层政治、地方组织、历史研究、当前验证顺序检索；
      证据不足时输出 unknown。
```

## 外部书目补录规则

- 外部书目必须能通过图书馆目录、出版社页面或正式书目资料核对。
- 每项应记录作者、书名、出版社、年份、语言、相关方法与核验日期。
- 不得在未核对的情况下填写页码或直接引语。
- 不得把本文件的内部操作化条目描述成小笠原欣幸的原话或原文。
