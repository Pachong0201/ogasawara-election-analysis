# data/

稳定历史选举事实与行政区数据。通过 `runtime/election_loader.py` 与
`runtime/data_readiness.py` 读写。

- `elections/<election_type>/<year>/<jurisdiction>.jsonl`
- `geography/administrative_areas/administrative_area.yaml`
- `geography/boundary_versions/`

不要在此目录存放未经验证的动态资料；动态资料放入 `cache/`。
