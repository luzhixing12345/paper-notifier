# paper-notifier

系统A会论文更新速览过滤，用于浏览 OSDI、NSDI、SOSP、ASPLOS、EuroSys、FAST、DAC、ISCA、MICRO、HPCA、SIGMOD、SIGCOMM 近 5 年录稿论文，并尽量补齐摘要。

## 使用方式

```bash
python3 app.py build-cache
python3 app.py serve
```

打开浏览器访问：

- 本地访问：`http://127.0.0.1:12315`
- 远程访问：`http://<本机IP>:12315`

服务启动时会通过访问 `10.255.255.255` 自动探测本机 IP，并在终端输出可访问地址。

也可以一步完成：

```bash
python3 app.py
```

默认行为是检查缓存；如果缺少新增会议或缺失年份，再增量构建缓存，然后启动本地网页服务。

## 数据来源

- 论文列表：DBLP `search/publ/api`
- 原始摘要：论文原始页面 / Crossref / OpenAlex
- 中文翻译：在线翻译接口

## 说明

- 服务会把拉取结果缓存在 `paper_cache/`
- 目录结构为 `paper_cache/<year>/<conference>/info.json`
- 网页服务本身只读取本地缓存，不再实时联网抓取
- `python3 app.py build-cache` 默认只补新增会议和缺失年份，不会全量重建已有年份
- 当前支持 `osdi`、`nsdi`、`sosp`、`asplos`、`eurosys`、`fast`、`dac`、`isca`、`micro`、`hpca`、`sigmod`、`sigcomm`
