# resume-screener

[![tests](https://github.com/czd1606391432-hash/resume-screener/actions/workflows/tests.yml/badge.svg)](https://github.com/czd1606391432-hash/resume-screener/actions/workflows/tests.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.8%2B-blue.svg)

把一批 PDF / Word 简历放进文件夹，按你定的规则自动分成「通过 / 淘汰 / 失败」三堆，通过的人汇总成一张表。之后可以拿一段岗位要求去表里反选候选人，也可以按岗位和简历出一套面试题。

规则是可配置的硬条件（学历类型、院校清单、公司黑名单），不是让模型凭感觉打分。模型只负责把简历读成结构化信息，合不合格由规则判定，每一个淘汰都有明确的原因。

> Batch résumé screening for Chinese-language résumés: PDF/DOCX in, rule-based pass/reject with reasons, an Excel roster out. Also matches a job description against the roster and drafts interview questions. Works with Anthropic-compatible or OpenAI-compatible LLM endpoints.

## 运行效果

下面是接真实模型、用 4 份虚构简历跑出来的原始输出（路径做了简化）：

```text
$ python3 -m resume_screener -w ws process
已加载院校清单 49 所
发现 4 份简历,开始处理
--- 处理 示例简历-张三.docx ---
  通过:张三 → 简历/通过/张三-20261112-105526/,汇总表第 1 行
--- 处理 示例简历-李四.docx ---
  淘汰(规则):毕业院校「示例学院」不在院校清单
--- 处理 示例简历-王五.docx ---
  淘汰(快筛):学历类型不合格:教育经历含「函授」
--- 处理 示例简历-赵六.docx ---
  通过:赵六 → 简历/通过/赵六-20261112-105533/,汇总表第 2 行
处理完成:通过 2,淘汰 2,失败 0

$ python3 -m resume_screener -w ws match -f examples/jd.example.txt -n 5 --include-failed
硬性条件: {"degree_min": "本科", "experience_min": 5, "locations": ["长沙"], ...}
软性关键词: 后端开发、Python、Go、微服务拆分、Kubernetes、支付系统、订单系统 ...
汇总表共 2 人,硬性条件通过 1 人,淘汰 1 人

1. 张三  匹配度 45%
   本科 · 清华大学 · 计算机科学与技术 · 工作 9 年 · 长沙
   命中关键词: go、kubernetes、python、订单系统、高级后端工程师

✗ 赵六:所在地 上海 不在 长沙
```

## 能做什么

| 功能 | 说明 |
|---|---|
| **批量甄选** | 提取文本 → 快筛 → 模型结构化解析 → 按规则判定。通过的移到「通过」并写进汇总表，淘汰的改名后移到「淘汰」，读不出来的移到「失败」。 |
| **JD 反选** | 给一段岗位要求，模型分出硬性条件和软性关键词；硬性条件逐条过滤，剩下的人按关键词打分排序。 |
| **面试题** | 按岗位和简历出题（技术 / 行为 / 反问），每题带评估维度和追问，可导出 Markdown、JSON 或 PDF。 |
| **水印剔除** | 招聘平台导出的 PDF 常带逐字平铺的水印，会把姓名、学历劈开。程序按版面位置识别并剔除水印字符。 |

## 处理流程

```text
待处理/*.pdf|docx
  → 提取文本（含水印剔除）
  → 快筛：教育经历里明确写了函授、专科等，直接淘汰，不调模型
  → 模型把简历解析成结构化信息（回复不是合法 JSON 时，把错误喂回去让它重出，最多 3 次）
  → 规则判定：学历类型 / 院校清单 / 公司黑名单
      通过 → 通过/<姓名>-<时间>/ ，汇总表加一行（带简历超链接）
      淘汰 → 淘汰/ ，日志里写明原因
      失败 → 失败/ （文本为空、模型解析失败）
```

两条设计原则：

- **没写清楚的默认放行**。只有模型明确判定「非全日制」才淘汰；大多数简历不写「统招」「全日制」这几个字，「没写就拒」会系统性误杀。
- **提到不等于任职**。公司黑名单只查工作经历里的公司字段；「参与过与某公司的联合项目」不会被淘汰。

## 快速开始

需要 Python 3.8 以上。

**1. 安装**

```bash
git clone https://github.com/czd1606391432-hash/resume-screener.git && cd resume-screener
```

```bash
pip install -r requirements.txt
```

**2. 建一个工作目录**

```bash
python3 -m resume_screener init my-workspace
```

目录里会有 `rules.json`（规则）、`院校清单.csv`（示例清单）、`校名别名.json`，以及「简历/待处理」等几个文件夹。

**3. 配置模型密钥**

默认接 Anthropic 的接口。密钥只从环境变量读，不写进任何文件：

```bash
export LLM_API_KEY=你的密钥
```

换成别的模型服务（OpenAI 兼容接口、Anthropic 兼容接口都可以）见 [docs/llm.md](docs/llm.md)。

**4. 放入简历并运行**

没有现成简历的话，可以先生成几份虚构的示例：

```bash
python3 examples/make_samples.py my-workspace/简历/待处理
```

```bash
python3 -m resume_screener -w my-workspace process
```

跑完后看 `my-workspace/输出/汇总表.xlsx` 和 `处理日志.log`。

**5. 按岗位要求反选**

```bash
python3 -m resume_screener -w my-workspace match -f examples/jd.example.txt -n 5
```

**6. 出面试题**

```bash
python3 -m resume_screener -w my-workspace interview "高级后端工程师" 某份简历.pdf -o 面试题.md
```

## 规则

规则都在工作目录的 `rules.json` 里，每一项都可以开关。完整说明见 [docs/rules.md](docs/rules.md)。

| 规则 | 默认 | 含义 |
|---|---|---|
| `education.block_associate` | 开 | 最高学历是专科的淘汰 |
| `education.reject_part_time` | 开 | 模型**明确判定**非全日制 / 非统招的淘汰；没写清楚的放行 |
| `education.reject_upgrade` | 开 | 专升本的淘汰 |
| `education.exclude_keywords` | 一组词 | 教育经历段里出现这些词（函授、自考……）直接淘汰，不调模型 |
| `schools.require_in_list` | 开 | 最高学历的毕业院校必须在院校清单里 |
| `company_blacklist` | 空 | 在这些公司任过职的淘汰 |
| `match.allow_gender_filter` / `allow_age_filter` | 关 | JD 反选时是否按性别 / 年龄过滤 |

**院校清单要换成你自己的。** 自带的 `院校清单.csv` 只是演示格式，随手列了几十所知名高校，不代表任何评价口径。清单可以是 `.csv`（每行「校名,分组」）或 `.xlsx`（每个工作表一个分组）。

校名匹配做了不少容错：常见简称（北大、华科）、括号注记、中英文连写、「××大学××管理学院」算本校而「××大学××城市学院」这类独立学院不算。想知道某个写法能不能匹配上：

```bash
python3 -m resume_screener -w my-workspace check-school "华中科技大学同济医学院"
```

## 命令

| 命令 | 作用 |
|---|---|
| `init [目录]` | 新建工作目录 |
| `process` | 甄选「待处理」里的全部简历。`--limit N` 只处理前 N 份，`--json` 打印每份的结果 |
| `match "JD" 或 -f 文件` | JD 反选。`-n` 取前几名，`--include-failed` 列出被淘汰的人和原因，`--json` |
| `interview 岗位 简历` | 出面试题。`-o` 输出到 `.md` / `.json` / `.pdf` |
| `extract 文件` | 只提取并打印文本，用来看水印剔除效果、排查问题 |
| `check-school 校名` | 看一个校名写法能不能匹配上院校清单 |

工作目录用 `-w` 指定（写在子命令前面），默认是当前目录。

## 数据和合规

- **简历是个人信息。** 简历文本会发送给你配置的模型服务，请确认这符合你们的数据处理要求和候选人的授权范围。除此之外程序不向任何地方发送数据，也不把简历保存到工作目录以外的地方。
- **筛选规则由使用者负责。** 学历、院校这些条件是否合适、是否符合当地法规，需要你们自己判断。
- **性别和年龄默认不参与筛选。** JD 里写了这两项，反选时默认忽略并在结果里明示；确有合法依据需要启用的，在 `rules.json` 的 `match` 段里显式打开。
- 模型解析可能出错。淘汰结果建议抽查，「失败」文件夹里的简历需要人工处理。

## 已知局限

- **验证状态**：这个项目是从一套实际使用过的工具里抽出来重构的，筛选规则沿用了原来验证过的逻辑。重构后的 v0.1.0 跑过 60 个离线测试，并接真实模型（一个 Anthropic 兼容接口）用 4 份虚构简历把甄选、JD 反选、面试题三个命令完整跑通，结果与预期一致。只试过这一个模型和这几份 Word 样本，真实的 PDF 简历在这个版本上没有跑过。
- **不支持扫描件和图片简历**（没有 OCR），会进「失败」。`.doc` 也不支持，需要先另存为 `.docx`。
- **水印剔除**只针对「逐字平铺、等间距」这一种水印。测试用的是程序合成的 PDF，不是真实平台导出的文件。
- **解析沙箱只在 Linux 上默认启用**（内存上限依赖 Linux 的机制），目前只在 macOS 上测过子进程的调用流程，内存上限是否生效没有实测。
- **JD 反选的关键词是按全等比较的**。JD 说「K8s」、简历写「Kubernetes」对不上；软性分数只适合粗排，不能当结论。
- **校名匹配**：「××大学××分校 / ××校区」按本校算，个别实际是独立办学的分校会被放过；外文校名需要在 `校名别名.json` 里自己补。
- **快筛依赖能截到「教育经历」段**。截不到时不在快筛里判，交给模型，只是多花一次调用。
- 只有命令行，没有图形界面。原工具的网页界面和其它业务耦合较深，没有带过来。
- 只处理中文简历；规则和提示词都是按中文简历的习惯写的。

## 测试

```bash
python3 -m unittest discover -s tests
```

60 个用例，不联网、不需要密钥。测试里的简历全部是虚构的。

## 许可

MIT
