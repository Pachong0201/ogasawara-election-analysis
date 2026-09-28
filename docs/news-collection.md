# 多源新闻采集与本地事件库

## 分析主线

历史票型基准 → 当前候选人格局 → 7/14/30 日事件 → 地方关系研究 → 同源民调校准 → 当前竞争结构。
采集层提供可回溯证据，不生成当选概率，不把新闻篇数当作选情强弱。

## 已实现的运行链

后台采集进程读取 `config/news_sources.yaml`，RSS/Atom、受限 HTML 列表和叶级 Sitemap 适配器统一输出链接。发现和正文任务持久化至 `cache/news/news.sqlite3`。机器人与 CLI 默认使用 `LocalNewsBackend` 查询本地库，不在回复链路等待新闻网站。

数据库使用 SQLite WAL；每次操作独立连接，任务领取采用事务及租约。正文版本、文章首次发现时间、来源运行记录、失败退避、主机间隔和事件修订均持久化。重启后恢复到期任务。运行目录必须可写，数据库不要放网络共享盘；备份使用 SQLite backup API 或停进程后整体备份，不能只复制正在写入的主文件而忽略 WAL。

## 从当前版本升级

1. 更新代码、安装 `requirements.txt`。
2. **将现有 `.env` 的 `OGASAWARA_BOT_RETRIEVAL=gdelt` 改为 `local`。** 环境变量优先于文件；旧部署显式配置不会被代码默认值覆盖。
3. 在一个终端启动采集器；在另一个终端启动飞书机器人：

```bash
python -m runtime.news_collector
```

```bash
python -m bot
```

Windows 可为采集器单独建立计划任务，执行同一 Python 环境的 `-m runtime.news_collector --once`，起始目录设为仓库根目录，每 5 分钟执行，禁止任务重叠。常驻模式每 10 秒检查队列，各来源默认每 15 分钟发现一次；正文任务独立推进。不要改动已有其他项目的计划任务。

运维与手动检查：

```bash
python -m runtime.news_collector --once --max-jobs 40
python -m runtime.news_collector --status
python -m runtime.news_collector --refresh --once
python -m runtime.cli run --county "高雄市" --year 2026 --type county_mayor --mode offline --retrieval-provider local
```

`--max-jobs` 是轮数上限，每轮最多执行一个发现任务和一个正文任务。`--refresh` 只提前健康来源的下次检查，不绕过429退避。CLI 的 `--retrieval-inbox` 仍优先使用原宿主检索桥；与本地库合并时请先导入链接再跑采集。

`offline` 可以读本地已采集资料，禁止联网；原有 `insufficient_current_data` 保守标签继续保留，不将离线缓存宣称为实时全量资料。`disabled` 完全停用新闻检索；`gdelt` 可显式恢复旧后端，但本地默认路径不调用 GDELT。

## 种子来源与覆盖边界

| 来源 | 默认状态 | 发现入口 |
|---|---|---|
| 中央社政治 | 启用 | FeedBurner RSS，加独立站内栏目列表 |
| 公视新闻 | 启用 | Atom |
| 联合政治 | 启用 | 栏目列表；旧RSS仅返回空条目，未采用 |
| 自由政治 | 禁用 | 本次端点探测403；不绕过限制 |

RSS 返回200不代表正文一定允许抓取，也不代表端点长期稳定。中央社两个入口使用同一个 publisher_id，不计作两个独立来源。默认种子尚未覆盖每个县市的地方媒体与组织，首次上线也不具备完整30日历史；`history_complete=false` 固定保守标记，不能因来源都返回200就认为覆盖完整。

增加来源时，在 YAML 声明 `id / adapter / url / article_domains / publisher_id / source_kind / source_grade`，可选 `jurisdictions / article_pattern / interval_seconds / max_links`。采集器重启加载配置；禁用来源后停止其发现和正文任务，历史证据仍保留。列表抓取建议必须限定文章 URL 模式。Sitemap 必须配置具体叶级文件，遇索引会明确报错，不无限递归。列表与 Sitemap 的更新时间不冒充文章发布时间。

官方、政党和候选人来源可走相同采集接口，但只有明确配置为媒体 C 级的来源进入媒体事件解析。官方原文保持待核验线索，登记和民调等正式事实仍使用现有专门 Adapter 与门禁；没有新增“政府域名即自动核实”的规则。

## 按需研究和历史补采

本地无结果或地方关系研究需要补查时，在线后端将问题写入持久研究队列。查询不会等待外部搜索，也不会自动调用付费搜索服务。导出交给具有 Web/Search 能力的宿主：

```bash
python -m runtime.news_collector --research-requests
```

宿主搜索、人工收集或历史归档输出 JSONL，每行形如：

```json
{"source_id":"cna_politics","url":"https://www.cna.com.tw/news/aipl/实际文章编号.aspx","title":"原始标题","published_at":"2026-09-23T09:00:00+08:00"}
```

这里的 URL 是格式示例，需替换为真实结果。导入：

```bash
python -m runtime.news_collector --import-links backfill.jsonl --once
```

仅接受启用来源允许域名下的 HTTPS 链接；外部传入的正文、核验等级与 verified 标记均不接受，重新经受控正文读取。重复导入不会重复创建文章。研究队列是宿主交接记录，当前不自动关闭；来源与事件仍由证据链验证。还未配置自动商业搜索 API、22县市专属来源包或全站历史爬取，不能声称已实现这些覆盖。

## 证据与时间规则

- 标题、无法读取的正文、未知发表日期均不能生成新鲜媒体事件。
- 原文版本按实际观察时间保存。历史 `as_of` 只能读取当时已观察到的版本；今天补采旧新闻不能伪装成系统昨天已掌握的证据。
- 日期参数按台湾时间日末截止，带时区时间戳按精确时刻截止；未来截止值不允许读取未来资料。
- 媒体转载依据 publisher_id、明确中央社署名及正文相似度保守合并。该规则不是通用语义来源识别器，仍可能漏掉改写转载。
- 政党／候选人保持 D/E；官方材料不进入统一 C 级媒体事件通道。
- 摘录发现否认、更正、撤稿或澄清标志时标记 `requires_review`，不作为独立研究触发。这是保守规则，会有误报，不能替代人工逐主张核验。
- 已有文章加入佐证时通过文章证据关系保持事件编号；内容变化另存事件版本，Snapshot Delta 列出 `updated_event_ids`。跨选举范围隔离；历史回放不更新实时身份索引。
- 事件仍是按报道日期形成的代理记录，不声称精确识别现实事件发生时刻；完全不共享文章的报道簇，可能需要人工合并。

## 稳定性与网络

429遵守 Retry-After，否则指数退避加随机间隔；失败来源独立退避，正文暂时失败不删除旧版本。同主机跨进程至少间隔2秒，另遵循已有 robots 检查。最近7天发现的文章每6小时复查一次，之后停止自动复查；更久的更正需要后续专项刷新。

请求保留域名、公开DNS、robots、大小和重定向检查。发现端点不自动跟随重定向，重定向需将实际允许端点写入配置；正文读取器沿用原有受控跳转。正文失败状态和来源失败状态分别统计；“发现成功”不等于“正文成功”。

若出现 `unsafe_source_url` 或 `unsafe_dns`，检查本机 DNS、代理和真实站点解析，不要关闭安全检查。本次执行环境经代理可以探测种子端点，但本地 DNS 不可解析，因此生产抓取器安全拒绝；没有据此宣称真实端到端采集通过。请在实际部署机启动采集器，检查 `--status` 中成功来源、正文 `read` 数量和错误，再验证飞书输出。

## 验证

```bash
python -m pytest -q
```

新增测试覆盖任务幂等、429故障隔离与重启退避、租约恢复、主机间隔、正文更正与回退版本、RSS/Atom/列表/Sitemap、导入安全、历史时间边界、离线 Pipeline、转载合并、非媒体等级边界、事件修订和默认后端接线。CI配置未改动。
