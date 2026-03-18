# paper-notifier

快速筛选是否感兴趣的系统会议期刊录用论文，当有新会议/期刊更新时自动发送邮件通知。

![20260318230916](https://raw.githubusercontent.com/learner-lu/picbed/master/20260318230916.png)

## 在线预览

https://paper-notifier.vercel.app

> 首次加载可能会有点卡

- 目前支持的会议：OSDI、NSDI、SOSP、ASPLOS、EUROSYS、FAST、DAC、ISCA、MICRO、HPCA、SIGMOD、SIGCOMM、USENIX
- 目前支持的期刊：TACO、TCAD
- 可以任意扩展，会议列表和抓取年限（默认近5年） [CONFERENCE.txt](CONFERENCE.txt) 和 [JOURNAL.txt](JOURNAL.txt) 里维护。

> 喜欢/不喜欢/已看完数据保存在您的浏览器缓存中，完全本地

## 快速开始

```bash
pip install -r requirements.txt
```

```python
python3 build-cache.py
```

执行效果如下（如果从0爬取的话），有的会议/期刊爬的速度比较慢

```bash
$ python build-cache.py
Building missing cache data...
conferences:  88%|████████████████████████████████████████████████████████████        | 15/17 [00:17<00:01,  1.16conf/s]
ppopp years:   0%|                                                                              | 0/5 [00:00<?, ?year/s]
ppopp 2026 抓原始摘要:  16%|█████████                                                 | 8/51 [00:50<02:51,  3.98s/paper]
ppopp 2025 抓原始摘要:  16%|█████████▎                                                | 8/50 [00:44<01:02,  1.50s/paper]
ppopp 2024 抓原始摘要:  18%|██████████▎                                               | 8/45 [00:48<02:08,  3.48s/paper]
ppopp 2023 抓原始摘要:  19%|██████████▊                                               | 8/43 [00:49<01:37,  2.80s/paper]
sc years:  25%|██████████████████▎                                                      | 1/4 [00:26<01:18, 26.05s/year]
sc 2025 抓原始摘要:   2%|▉                                                           | 7/433 [00:44<16:48,  2.37s/paper]
sc 2024 翻译中文摘要:  36%|████████████████████▍                                   | 122/335 [00:09<00:14, 14.54paper/s]
sc 2023 抓原始摘要:   2%|█▎                                                          | 8/356 [00:46<16:08,  2.78s/paper]
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

## FAQ

**1. arxiv?**

arxiv更新比较频繁，而且其实 github 上面已经有arxiv-daily这种项目了。我个人认为技术本身不是难点，难的是人的精力跟不过来，即使每日推送最新论文消息也不见得能有精力每天都关注最新进展

我个人更希望的是保持对于系统领域最新进展的关注，当有新的会议更新了（OSDI 2090）可以提醒我看一下有哪些最新的工作，可能不会看论文细节，只是简单了解一下有哪些人在做哪些事情，也可以理解为保持手感

**2. 能否支持其他会议/期刊？**

可以的，您可以按照下面的操作clone本仓库修改配置文件后重新爬取即可，或者您可以提一个issue说明想要看的会议我来更新仓库，您可以直接访问[原地址](https://paper-notifier.vercel.app)，这件事情很简单

**3. 下载论文？预览论文？**

网页里有一个下载按钮，但是点进去实际上只是跳转到对应的网页，因为论文不是公开数据库，需要学生认证/校园认证等等，需要身份信息才能下载，所以只能做到跳转过去您手动点击下载

预览论文同理

**4. LLM API KEY？**

这一步能做的事情其实很多了，比如把pdf丢给大模型问答，打分等等，这个项目不想做那么复杂，简单一些，获取数据，展示数据，这样就好

## 数据来源

- 论文列表：DBLP `search/publ/api`
- 原始摘要：论文原始页面 / Crossref / OpenAlex
- 中文翻译：在线翻译接口

## 说明

- 服务会把拉取结果缓存在 `paper_cache/`
- 目录结构为 `paper_cache/<year>/<conference>/info.json`
- 默认只补新增会议和缺失年份，不会全量重建已有年份
- 在补到新的 `conference/year` 缓存时会发邮件通知
