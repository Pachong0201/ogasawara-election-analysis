# 测试

本目录包含 Skill V1.0 的契约测试与验收测试入口。

## 运行

```bash
python3 -m unittest discover -s tests -v
```

测试只依赖 Python 标准库与 PyYAML。若环境没有 PyYAML，请先安装或改用可用的 YAML 解析器。

## 测试内容

- 推荐文件结构是否完整；
- YAML 配置与 schema 是否可解析；
- 核心分析路径是否存在；
- 14 条硬性禁止规则是否完整；
- POLL-01 至 POLL-07 是否存在；
- 默认输出模板八个部分是否存在；
- 宜兰测试是否包含 Test A、Test B、Test C；
- `examples/yilan/` 是否被明确标注为测试资料而非运行依赖。
