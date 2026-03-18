# paper-notifier

快速筛选是否感兴趣的系统会议期刊录用论文，当有新会议/期刊更新时自动发送邮件通知。

![20260318230916](https://raw.githubusercontent.com/learner-lu/picbed/master/20260318230916.png)

## 在线预览

https://paper-notifier.vercel.app

- 目前支持的会议：OSDI、NSDI、SOSP、ASPLOS、EUROSYS、FAST、DAC、ISCA、MICRO、HPCA、SIGMOD、SIGCOMM、USENIX
- 目前支持的期刊：TACO、TCAD
- 可以任意扩展，会议列表和抓取年限（默认近5年） [CONFERENCE.txt](CONFERENCE.txt) 和 [JOURNAL.txt](JOURNAL.txt) 里维护。

> 喜欢/不喜欢/已看完数据保存在浏览器缓存中

## 快速开始

```bash
pip install -r requirements.txt
```

```python
python3 build-cache.py
```

最终所有数据按年份保存在 paper_cache 下，并汇总到 assets/papers-data.json，打开 index.html 即可浏览。

## 设置定时任务

我们希望当有新会议/期刊更新时自动发送邮件通知我，首先需要更新 `EMAIL.txt` 填入你的邮箱

然后执行如下脚本

```bash
./setup_crontab.sh
```

它会创建一个每天北京时间8点的定时任务，爬取数据并检查是否有新的信息，如果有则发送一封邮件(如下所示)

```txt
Paper Notifier: OSDI [2025] 53 Papers

Detected a newly cached venue-year entry during build-cache.

Conference: OSDI (osdi)
Year: 2025
Papers: 53
```

> 您可以简单删除 paper_cache/2025/osdi 目录然后重新执行 python3 build-cache.py 以查看邮件效果

## 其他会议和期刊

会议列表和抓取年限（默认近5年） [CONFERENCE.txt](CONFERENCE.txt) 和 [JOURNAL.txt](JOURNAL.txt) 里维护。

您可以简单新增想看到的会议/期刊，比如 hotos，然后重新运行 build-cache.py 即可。如果想要更早期的会议修改文件中的 lookback_years 即可

有的会议和期刊可能和缩写的名字有所出入，您可以使用 resolve_conference.py 脚本进行检查，如下所示

```bash
$ python resolve_conference.py hotos
input_key: hotos
suggested_label: HotOS
configured: no
slug_candidates:
  - hotos
exists_on_dblp: yes
resolved_kind: conf
resolved_dblp_slug: hotos
dblp_url: https://dblp.org/db/conf/hotos/index.html
page_title: dblp: Workshop on Hot Topics in Operating Systems (HotOS)
heading: USENIX Workshop on Hot Topics in Operating Systems (HotOS)
suggested_mapping: {'label': 'HotOS', 'dblp_slug': 'hotos', 'venue_kind': 'conf'}
```

> 在 [CONFERENCE.txt](./CONFERENCE.txt) 结尾追加 hotos 即可

```bash
$ python resolve_conference.py taco
input_key: taco
suggested_label: TACO
configured: yes
configured_kind: journals
configured_label: TACO
slug_candidates:
  - taco
exists_on_dblp: yes
resolved_kind: journals
resolved_dblp_slug: taco
dblp_url: https://dblp.org/db/journals/taco/index.html
page_title: dblp: ACM Transactions on Architecture and Code Optimization (TACO)
heading: ACM Transactions on Architecture and Code Optimization (TACO)
suggested_mapping: {'label': 'TACO', 'dblp_slug': 'taco', 'venue_kind': 'journals'}
```

> 在 [JOURNAL.txt](./JOURNAL.txt) 结尾追加 taco 即可

## 数据来源

- 论文列表：DBLP `search/publ/api`
- 原始摘要：论文原始页面 / Crossref / OpenAlex
- 中文翻译：在线翻译接口

## 说明

- 服务会把拉取结果缓存在 `paper_cache/`
- 目录结构为 `paper_cache/<year>/<conference>/info.json`
- 默认只补新增会议和缺失年份，不会全量重建已有年份
- 在补到新的 `conference/year` 缓存时会发邮件通知
