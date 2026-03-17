# 论文会议近 3 年录稿论文浏览

本项目提供一个本地 Python HTTP 服务，用于浏览 OSDI、NSDI、SOSP 近 3 年录稿论文，并尽量补齐摘要。

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

默认行为是先构建缓存，再启动本地网页服务。

## 数据来源

- 论文列表：DBLP `search/publ/api`
- 原始摘要：论文原始页面 / Crossref / OpenAlex
- 中文翻译：在线翻译接口

## 说明

- 服务会把拉取结果缓存在 `data/papers_cache.json`
- 网页服务本身只读取本地缓存，不再实时联网抓取
- 如果需要更新数据，重新执行 `python3 app.py build-cache`
- 目前只支持 `osdi`、`nsdi`、`sosp`
