# 22县市社会基础层官方数据

新增 `config/county_context_sources.yaml` 与 `runtime.county_context_catalog`，覆盖五类官方数据：

- 2020人口普查常住人口年龄结构；
- 全国宗教资讯系统寺庙资料；
- 全国性社会团体按地区名册；
- 农业部农会会员数；
- 农业部渔会会员数。

目录记录只以 `verified_source_catalog` 进入 retrieval staging，不可直接晋升。取得真实官方 CSV/JSON/XML 后，再用离线导入器记录 SHA256、原始行与县市映射：

```bash
python -m runtime.county_context_catalog --stage-catalog --all-counties

python -m runtime.county_context_catalog \
  --source-id moa_fishermen_association_members \
  --file D:/official/fisher-members.csv
```

导入产物位于 `data/context/<county>/`，清单位于 `data/manifests/`。年龄结构在字段完整时额外生成未满15岁、15—64岁及65岁以上人口及占比；所有社会团体、宗教、农渔会资料只作为组织基础，不得据存在、数量或会员规模推断政治支持、动员效果或投票行为。

“关键地方议题”不从静态目录自动生成。该类知识必须按县市和时间范围进行近期检索，优先A/B级主管机关资料；只有媒体资料时要求两个独立C级正文来源，并保留反证/更正检查。
