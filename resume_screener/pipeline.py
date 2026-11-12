"""批处理主流程:待处理文件夹里的每份简历 → 通过 / 淘汰 / 失败。

  提取文本 → 快筛(不调模型)→ 模型结构化解析 → 按规则判定
  → 通过:移到「通过/<姓名>-<时间>/」,汇总表加一行
  → 淘汰:改名后移到「淘汰/」     → 提取或解析失败:原样移到「失败/」
"""
import shutil
import traceback
from datetime import datetime
from pathlib import Path

from . import filter as flt
from .llm import LLMConfigError
from .extract import SUPPORTED, extract, normalize
from .parse import parse_resume
from .rename import guess_name_from_text, name_from_pdf_meta, new_filename, safe_name
from .summary import append_candidate


def _log(ws, msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    ws.log_file.parent.mkdir(parents=True, exist_ok=True)
    with ws.log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _move(src, target_dir, new_name):
    target_dir.mkdir(parents=True, exist_ok=True)
    target, i = target_dir / new_name, 1
    while target.exists():      # 同名不覆盖
        target = target_dir / ("%s-%02d%s" % (Path(new_name).stem, i, Path(new_name).suffix))
        i += 1
    shutil.move(str(src), str(target))
    return target


def process_one(ws, file, chat=None):
    """处理一份简历,返回 {"file", "result": passed|rejected|failed, "reason", "name", "target"}。"""
    file = Path(file)
    rn = ws.rules.get("rename") or {}
    named = lambda name: new_filename(name, rn.get("format") or "{name}-简历-{ts}", rn.get("ts_pattern") or "%Y%m%d-%H%M%S")
    out = {"file": file.name, "result": "failed", "reason": "", "name": "", "target": ""}
    _log(ws, "--- 处理 %s ---" % file.name)

    def fail(reason):
        out.update(reason=reason, target=str(_move(file, ws.failed, file.name)))
        _log(ws, "  失败:%s → %s" % (reason, out["target"]))
        return out

    def reject(reason, name, stage):
        out.update(result="rejected", reason=reason, name=name,
                   target=str(_move(file, ws.rejected, named(name) + file.suffix.lower())))
        _log(ws, "  淘汰(%s):%s → %s" % (stage, reason, out["target"]))
        return out

    try:
        text = normalize(extract(file))
    except Exception as e:
        return fail("提取文本失败:%s" % e)
    if not text.strip():
        return fail("文本为空(可能是扫描件或图片)")

    ok, why = flt.quick_filter(text, ws.rules)      # 明显不合格的不送模型,省一次调用
    if not ok:
        return reject(why, guess_name_from_text(text), "快筛")

    try:
        info = parse_resume(text, chat=chat)
    except LLMConfigError:
        raise                     # 密钥问题:这份简历留在原地,整批停下
    except Exception as e:
        return fail("模型解析失败:%s" % e)
    name = info.get("name") or guess_name_from_text(text) or (name_from_pdf_meta(file) if file.suffix.lower() == ".pdf" else "")
    info["name"] = name

    ok, why = flt.judge(info, ws.rules, ws.pool, ws.aliases)
    if not ok:
        return reject(why, name, "规则")

    person_dir = ws.passed / ("%s-%s" % (safe_name(name) or "未知姓名", datetime.now().strftime("%Y%m%d-%H%M%S")))
    target = _move(file, person_dir, named(name) + file.suffix.lower())
    out.update(result="passed", reason="ok", name=name, target=str(target), info=info)
    try:
        seq = append_candidate(ws.summary_xlsx, info, target)
        _log(ws, "  通过:%s → %s,汇总表第 %d 行" % (name or "未知姓名", target, seq))
    except Exception as e:
        out["reason"] = "已通过,但汇总表写入失败:%s" % e
        _log(ws, "  通过,但汇总表写入失败:%s" % e)
    return out


def run(ws, chat=None, limit=None):
    """处理待处理文件夹里的全部简历,返回每份的结果列表。"""
    ws.ensure_dirs()
    if (ws.rules.get("schools") or {}).get("require_in_list"):
        _log(ws, "已加载院校清单 %d 所" % len(ws.pool))
    files = sorted(p for p in ws.inbox.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED + (".doc",))
    if limit:
        files = files[:limit]
    if not files:
        _log(ws, "没有待处理的简历(把 PDF / DOCX 放进 %s)" % ws.inbox)
        return []
    _log(ws, "发现 %d 份简历,开始处理" % len(files))
    results = []
    for f in files:
        try:
            results.append(process_one(ws, f, chat=chat))
        except LLMConfigError as e:
            _log(ws, "模型配置有问题,已停止(没处理的简历还在「待处理」里):%s" % e)
            raise
        except Exception as e:      # 一份出错不影响后面的
            _log(ws, "!! %s 处理异常: %s" % (f.name, e))
            traceback.print_exc()
            results.append({"file": f.name, "result": "failed", "reason": "处理异常:%s" % e, "name": "", "target": ""})
    count = lambda r: sum(1 for x in results if x["result"] == r)
    _log(ws, "处理完成:通过 %d,淘汰 %d,失败 %d" % (count("passed"), count("rejected"), count("failed")))
    return results
