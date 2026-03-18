# paper-notifier

系统A会论文更新速览过滤，用于浏览 OSDI、NSDI、SOSP、ASPLOS、EuroSys、FAST、DAC、ISCA、MICRO、HPCA、SIGMOD、SIGCOMM 近 5 年录稿论文，并尽量补齐摘要。

## 使用方式

```bash
python3 app.py build-cache
```

默认行为也是构建缓存并导出静态数据：

```bash
python3 app.py
```

## 静态页面模式

前端默认直接读取 `assets/papers-data.json`，不依赖后端查询接口。

基于缓存导出静态数据：

```bash
python3 app.py build-static
```

如果缓存还没生成，也可以直接：

```bash
python3 app.py build-cache
```

这会同时更新 `paper_cache/` 和 `assets/papers-data.json`。生成后可以把 `assets/` 目录直接部署到任意静态托管。

## 数据来源

- 论文列表：DBLP `search/publ/api`
- 原始摘要：论文原始页面 / Crossref / OpenAlex
- 中文翻译：在线翻译接口

## 说明

- 服务会把拉取结果缓存在 `paper_cache/`
- 目录结构为 `paper_cache/<year>/<conference>/info.json`
- 不再内置网页服务，`app.py` 只负责抓取、缓存和导出静态 JSON
- `python3 app.py build-cache` 默认只补新增会议和缺失年份，不会全量重建已有年份
- 当前支持 `osdi`、`nsdi`、`sosp`、`asplos`、`eurosys`、`fast`、`dac`、`isca`、`micro`、`hpca`、`sigmod`、`sigcomm`
