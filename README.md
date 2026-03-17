# 论文会议近 3 年录稿论文浏览

本项目提供一个本地 Python HTTP 服务，用于浏览 OSDI、NSDI、SOSP 近 3 年录稿论文，并尽量补齐摘要。

## 运行

```bash
python3 app.py
```

打开浏览器访问：

- 本地访问：`http://127.0.0.1:12315`
- 远程访问：`http://<本机IP>:12315`

服务启动时会通过访问 `10.255.255.255` 自动探测本机 IP，并在终端输出可访问地址。

## 数据来源

- 论文列表：DBLP `search/publ/api`
- 摘要补全：OpenAlex `works` API

## 说明

- 服务会把拉取结果缓存在 `data/papers_cache.json`
- 可通过 `http://127.0.0.1:12315/api/papers?conference=osdi&refresh=1` 强制刷新
- 目前只支持 `osdi`、`nsdi`、`sosp`
