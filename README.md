# paper-notifier

系统A会论文更新速览过滤，用于抓取配置文件中定义的会议录稿论文，并尽量补齐摘要。

## 使用方式

```bash
python3 build-cache.py build-cache
```

默认行为也是构建缓存并导出静态数据：

```bash
python3 build-cache.py
```

会议列表和抓取年限都在 [CONFERENCE.txt](/home/lzx/paper_abstract/CONFERENCE.txt) 和 [JOURNAL.txt](/home/lzx/paper_abstract/JOURNAL.txt) 里维护。

配置格式：

```txt
lookback_years=5
osdi
sigmod
```

- `lookback_years` 表示抓取近几年
- 每个会议或期刊一行，只写小写 key
- `CONFERENCE.txt` 走 `DBLP /db/conf/...`
- `JOURNAL.txt` 走 `DBLP /db/journals/...`
- 代码会自动把 key 转成展示名称和 `dblp_slug`

## 静态页面模式

前端默认直接读取 `assets/papers-data.json`，不依赖后端查询接口。

基于缓存导出静态数据：

```bash
python3 build-cache.py build-static
```

如果缓存还没生成，也可以直接：

```bash
python3 build-cache.py build-cache
```

这会同时更新 `paper_cache/` 和 `assets/papers-data.json`。生成后可以把 `assets/` 目录直接部署到任意静态托管。

## 数据来源

- 论文列表：DBLP `search/publ/api`
- 原始摘要：论文原始页面 / Crossref / OpenAlex
- 中文翻译：在线翻译接口

## 说明

- 服务会把拉取结果缓存在 `paper_cache/`
- 目录结构为 `paper_cache/<year>/<conference>/info.json`
- 不再内置网页服务，`build-cache.py` 只负责抓取、缓存和导出静态 JSON
- 支持的会议和期刊，以及抓取年限，分别由 `CONFERENCE.txt` 和 `JOURNAL.txt` 控制
- `python3 build-cache.py build-cache` 默认只补新增会议和缺失年份，不会全量重建已有年份
