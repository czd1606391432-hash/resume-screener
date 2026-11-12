"""命令行入口。"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, filter as flt, interview, llm, match_jd, pipeline
from .config import ConfigError, init_workspace, load_workspace
from .extract import extract, normalize


def _ws(a):
    ws = load_workspace(a.workspace)
    llm.configure(ws.rules.get("llm"))
    return ws


def cmd_init(a):
    ws, made = init_workspace(a.dir)
    print("工作目录:", ws.root.resolve())
    print("新建的文件:", "、".join(made) if made else "(都已存在,没有覆盖)")
    print("下一步:\n  1. 把院校清单.csv 换成你们自己的清单,按需要改 rules.json\n"
          "  2. 设置模型密钥,例如: export LLM_API_KEY=你的密钥\n"
          "  3. 把简历(PDF / DOCX)放进 %s\n  4. 运行: resume-screener -w %s process" % (ws.inbox, a.dir))


def cmd_process(a):
    ws = _ws(a)
    llm.api_key()      # 没有密钥就先报错,别等处理到一半
    results = pipeline.run(ws, limit=a.limit)
    if a.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "info"} for r in results], ensure_ascii=False, indent=2))
    return 1 if any(r["result"] == "failed" for r in results) else 0


def cmd_match(a):
    ws = _ws(a)
    jd = a.jd or (Path(a.file).read_text(encoding="utf-8") if a.file else "")
    if not jd.strip():
        raise ConfigError("请给出岗位要求:直接写在命令后面,或用 -f 指定文件")
    res = match_jd.match_workspace(ws, jd, top=a.top, include_failed=a.include_failed)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    print("模型对 JD 的理解:", res["thinking"])
    print("硬性条件:", json.dumps(res["hard"], ensure_ascii=False))
    if res["ignored"]:
        print("没有用来筛选的条件:%s(默认不按这些条件筛选,见 rules.json 的 match 段)" % "、".join(res["ignored"]))
    print("软性关键词:", "、".join(res["soft_keywords"]) or "(无)")
    print("汇总表共 %d 人,硬性条件通过 %d 人,淘汰 %d 人\n" % (res["total"], res["passed_count"], res["failed_count"]))
    if not res["total"]:
        print("汇总表是空的。先运行 process,让通过甄选的简历进入汇总表。")
    for i, c in enumerate(res["candidates"], 1):
        print("%d. %s  匹配度 %.0f%%" % (i, c["name"] or "未知", c["score"] * 100))
        print("   %s · %s · %s · 工作 %s 年 · %s" % (c["degree"], c["school"], c["major"], c["exp_years"] if c["exp_years"] not in (None, "") else "?", c["city"] or "所在地未知"))
        print("   命中关键词:", "、".join(c["common"]) or "(无)")
        print("   近况:", c["summary"])
        print("   简历:", c["resume_path"], "\n")
    for c in res["failed"]:
        print("✗ %s:%s" % (c["name"] or "未知", c["fail_reason"]))
    return 0


def cmd_interview(a):
    try:
        _ws(a)
    except ConfigError:      # 出面试题不依赖工作目录,没有 rules.json 就只用环境变量里的模型配置
        llm.configure(None)
    try:
        is_file = len(a.resume) < 1000 and Path(a.resume).is_file()
    except OSError:
        is_file = False
    text = normalize(extract(Path(a.resume))) if is_file else a.resume
    r = interview.generate_interview(a.job, text, difficulty=a.difficulty, n_tech=a.tech, n_behavior=a.behavior, n_reverse=a.reverse)
    if not r["ok"]:
        raise ConfigError("生成失败:%s" % r["error"])
    if a.output and a.output.lower().endswith(".pdf"):
        Path(a.output).write_bytes(interview.generate_interview_pdf(r["data"], a.job))
    elif a.output and a.output.lower().endswith(".json"):
        Path(a.output).write_text(json.dumps(r["data"], ensure_ascii=False, indent=2), encoding="utf-8")
    elif a.output:
        Path(a.output).write_text(interview.render_markdown(r["data"], a.job), encoding="utf-8")
    else:
        print(interview.render_markdown(r["data"], a.job))
        return 0
    print("已写入", a.output)
    return 0


def cmd_extract(a):
    print(normalize(extract(Path(a.file))))


def cmd_check_school(a):
    ws = load_workspace(a.workspace)
    ok = flt.school_in_pool(a.name, ws.pool, ws.aliases)
    print("「%s」%s院校清单内(清单共 %d 所)" % (a.name, "在" if ok else "不在", len(ws.pool)))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="resume-screener", description="批量简历甄选、JD 反选、面试题生成。")
    ap.add_argument("-w", "--workspace", default=os.environ.get("RESUME_SCREENER_HOME", "."), help="工作目录(默认当前目录)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("init", help="新建一个工作目录(规则、示例院校清单、各个文件夹)")
    p.add_argument("dir", nargs="?", default=".")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("process", help="甄选「待处理」文件夹里的全部简历")
    p.add_argument("--limit", type=int, help="最多处理几份")
    p.add_argument("--json", action="store_true", help="最后把每份的结果按 JSON 打出来")
    p.set_defaults(fn=cmd_process)

    p = sub.add_parser("match", help="JD 反选:按岗位要求从汇总表里挑人")
    p.add_argument("jd", nargs="?", help="岗位要求文本")
    p.add_argument("-f", "--file", help="从文件读岗位要求")
    p.add_argument("-n", "--top", type=int, default=5, help="取前几名(默认 5)")
    p.add_argument("--include-failed", action="store_true", help="同时列出被硬性条件淘汰的人和原因")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_match)

    p = sub.add_parser("interview", help="按岗位和简历生成面试题")
    p.add_argument("job", help="岗位名称")
    p.add_argument("resume", help="简历文件(PDF / DOCX / TXT),或直接给一段简历文字")
    p.add_argument("-o", "--output", help="输出文件:.md / .json / .pdf;不给就打印 Markdown")
    p.add_argument("--difficulty", default="自动", help="初级 / 中级 / 高级 / 自动(默认)")
    p.add_argument("--tech", type=int, default=3)
    p.add_argument("--behavior", type=int, default=2)
    p.add_argument("--reverse", type=int, default=1)
    p.set_defaults(fn=cmd_interview)

    p = sub.add_parser("extract", help="只提取并打印一份简历的文本(看水印剔除效果、排查问题用)")
    p.add_argument("file")
    p.set_defaults(fn=cmd_extract)

    p = sub.add_parser("check-school", help="看一个校名写法能不能匹配上院校清单")
    p.add_argument("name")
    p.set_defaults(fn=cmd_check_school)

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    try:
        return a.fn(a) or 0
    except (ConfigError, llm.LLMError, NotImplementedError, FileNotFoundError, RuntimeError, ValueError) as e:
        print("出错:", e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
