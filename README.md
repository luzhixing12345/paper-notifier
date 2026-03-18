# paper-notifier

快速筛选是否感兴趣的系统会议期刊录用论文，当有新会议/期刊更新时自动发送邮件通知。

## 在线预览

https://luzhixing12345.github.io/paper-notifier/

- 目前支持的会议：
- 目前支持的期刊：
- 可以任意扩展，会议列表和抓取年限（默认近5年） [CONFERENCE.txt](CONFERENCE.txt) 和 [JOURNAL.txt](JOURNAL.txt) 里维护。

## 快速开始

```bash
pip install -r requirements.txt
```

```python
python3 build-cache.py
```

最终所有数据按年份保存在 paper_cache 下，并汇总到 assets/papers-data.json，打开 index.html 即可浏览。

## 数据来源

- 论文列表：DBLP `search/publ/api`
- 原始摘要：论文原始页面 / Crossref / OpenAlex
- 中文翻译：在线翻译接口

## 说明

- 服务会把拉取结果缓存在 `paper_cache/`
- 目录结构为 `paper_cache/<year>/<conference>/info.json`
- 默认只补新增会议和缺失年份，不会全量重建已有年份
- 在补到新的 `conference/year` 缓存时会发邮件通知
