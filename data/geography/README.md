# data/geography/

行政区代码、层级、边界版本与 predecessor/successor 映射。

- `administrative_areas/administrative_area.yaml`：行政区版本模板，默认不预填未核实资料。
- `boundary_versions/`：行政区划变更映射；跨届比较前必须检查 `boundary_compatible()`。

记录字段至少包括：

```yaml
region_id:
name:
level:
parent:
valid_from:
valid_to:
boundary_version:
predecessor_regions:
successor_regions:
```
