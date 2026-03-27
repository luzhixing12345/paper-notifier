# paper-notifier

系统A会录用论文合集 + 会议更新通知

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

执行效果如下，本项目没有采用并行爬取数据，因为很容易被 doi.org 封禁ip，完全串行所以速度会慢一些，但胜在一劳永逸

```bash
$ python build-cache.py
Script started at 2026-03-19 16:30:48
Building missing cache data...
[conference 1/17] OSDI (osdi)
[OSDI] years=[2025, 2024, 2023, 2022]
[conference 2/17] NSDI (nsdi)
[NSDI] years=[2025, 2024, 2023, 2022]
[conference 3/17] SOSP (sosp)
[SOSP] years=[2025, 2024, 2023]
[conference 4/17] ASPLOS (asplos)
[ASPLOS] years=[2026, 2025, 2024, 2023, 2022]
[ASPLOS 2022] entry asplos2022 hits=81
[ASPLOS 2022][1/81] Adelie: continuous address space layout re-randomization for Linux drivers.
  -> abstract ok: source=DOI Page, chars=2194
  -> translation ok: chars=793
[ASPLOS 2022][2/81] A tree clock data structure for causal orderings in concurrent executions.
  -> abstract ok: source=DOI Page, chars=1677
  -> translation ok: chars=532
[ASPLOS 2022][3/81] Every walk's a hit: making page walks single-access cache hits.
  -> abstract ok: source=DOI Page, chars=1908
  -> translation ok: chars=613
```

最终所有数据按年份保存在 paper_cache/`<year>`/`<conference>` 下，并按照会议汇总到 assets/ 下，打开 index.html 即可浏览。

> full_miss_abstract.py 用于补全某一个会议某年缺失的论文摘要，爬 doi.org 如果太狠了会被封ip，有的摘要会遗漏，这个脚本可以帮助补齐内容
>
> python full_miss_abstract.py <conference> <year>
> 
> ```bash
> $ python full_miss_abstract.py asplos 2026
> 
> Processing 7 missing abstract(s) in /home/lzx/paper-notifier/paper_cache/2026/asplos/info.json
> [1/7] fetching TetriServe: Efficiently Serving Mixed DiT Workloads.
>   -> abstract ok: source=DOI Page, chars=1329
> [1/7] translating TetriServe: Efficiently Serving Mixed DiT Workloads.
>   -> translation ok: chars=519
>   -> saved
> [2/7] fetching Enabling Fast Networking in the Public Cloud.
>   -> abstract ok: source=DOI Page, chars=908
> [2/7] translating Enabling Fast Networking in the Public Cloud.
>   -> translation ok: chars=353
>   -> saved
> ...
> ```
>
> 最后再重新构建给 index.html 用的 json 索引
> ```bash
> python build-cache build-static
> ```

## 接受邮件提醒

### Github Action

最简单的方式是 fork 此项目，本项目已经设置了 github action，每天早上8点会自动执行脚本，检测更新，如果有更新会发送邮件。

本项目使用 e2me 进行邮件发送服务，您只需要添加两个secret环境变量 `E2ME_EMAIL` 和 `E2ME_PASSWD`，E2ME_EMAIL 为邮箱地址，E2ME_PASSWD 为邮箱的 smtp 密码，见 [e2me](https://github.com/luzhixing12345/e2me)

![20260327111117](https://raw.githubusercontent.com/learner-lu/picbed/master/20260327111117.png)

### 本地部署

首先您需要初始化您的邮箱服务才能发送邮件，使用 `e2me init` 初始化您的邮箱配置，见 [e2me](https://github.com/luzhixing12345/e2me)

初始化完成后执行脚本

```bash
./setup_crontab.sh
```

它会创建一个每天北京时间8点的定时任务，爬取数据并检查是否有新的信息，如果有则发送一封邮件

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
