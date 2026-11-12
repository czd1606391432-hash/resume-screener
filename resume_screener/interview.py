"""面试题生成:岗位 + 候选人简历 → 一套结构化面试题(技术 / 行为 / 反问),可导出 Markdown 或 PDF。

题目紧扣简历里的具体项目和技能;每道题带评估维度,以及「答得好 / 答得不好」时各自的追问。
"""
import os
import platform
import re
from datetime import datetime
from pathlib import Path

from . import llm

CATEGORIES = ("技术", "行为", "反问")

SYSTEM_PROMPT = """你是资深面试官,擅长为不同岗位设计结构化面试题。只输出 JSON,不要 markdown 包裹、注释或任何额外文字。

【按工龄调整路线】
- 工龄 ≤ 1 年(应届 / 实习):校招路线,重基础原理、学习能力、潜力,项目深度可以放宽
- 工龄 1-3 年:重项目深挖、独立解决问题
- 工龄 3-5 年:重复杂问题拆解、跨团队协作
- 工龄 5-8 年:重方案设计、技术或业务选型、影响力
- 工龄 8 年以上:重行业洞察、商业思维、组织建设

【设计原则】
1. 技术题要紧扣候选人简历里提到的具体项目和技能,不能泛泛而问
2. 行为题用 STAR 框架(情境 - 任务 - 行动 - 结果)
3. 反问题是留给候选人向面试官提问的环节:给出值得期待的好问题方向,用来观察他的思考深度
4. 每道题都要有 2 个评估维度,以及答得好、答得不好时各自的追问,方便面试官现场应变
"""


def build_user_prompt(job_title, resume_text, difficulty, n_tech, n_behavior, n_reverse):
    hint = "" if difficulty in ("自动", "auto", "", None) else "\n【指定难度】%s(按这个难度出题,不要再自行推断)" % difficulty
    return """【岗位】%s
【题量】技术 %d 道 / 行为 %d 道 / 反问 %d 道%s

【候选人简历】
%s

请按下面的 JSON 结构输出,字段顺序保持一致:

{
  "thinking": "你对这位候选人的判断和面试策略(两三句话)",
  "candidate_summary": {
    "name": "从简历里取姓名,取不到写「未知」",
    "years_of_experience": 工龄(数字,推断不了写 0),
    "level": "校招 / 初级 / 中级 / 高级 / 资深 / 专家",
    "path": "校招 / 社招",
    "background_summary": "一句话背景"
  },
  "questions": [
    {
      "category": "技术 / 行为 / 反问",
      "title": "题干(一到三句话,表述清楚)",
      "difficulty": "初级 / 中级 / 高级",
      "hints": ["提示关键词"],
      "key_points": ["答案要点"],
      "evaluation_dimensions": ["评估维度 1", "评估维度 2"],
      "follow_up_if_good": "候选人答得好时,用这个追问挖深度",
      "follow_up_if_bad": "候选人答得不好时,用这个追问看基础"
    }
  ]
}""" % (job_title, n_tech, n_behavior, n_reverse, hint, resume_text[:6000])


def generate_interview(job_title, resume_text, difficulty="自动", n_tech=3, n_behavior=2, n_reverse=1,
                       max_retries=2, chat=None):
    """返回 {"ok": bool, "data": 模型输出的 dict 或 None, "error": str 或 None, "attempts": int}。"""
    chat = chat or llm.chat
    if not (job_title or "").strip():
        return {"ok": False, "data": None, "error": "岗位不能为空", "attempts": 0}
    if not (resume_text or "").strip():
        return {"ok": False, "data": None, "error": "简历内容不能为空", "attempts": 0}
    messages = [{"role": "user", "content": build_user_prompt(job_title.strip(), resume_text.strip(), difficulty,
                                                              n_tech, n_behavior, n_reverse)}]
    err = ""
    for attempt in range(max_retries + 1):
        raw = ""
        try:
            raw = chat(messages, system=SYSTEM_PROMPT)
            data = llm.extract_json(raw)
            if isinstance(data.get("questions"), list) and data["questions"]:
                return {"ok": True, "data": data, "error": None, "attempts": attempt + 1}
            err = "回复里没有 questions"
        except llm.LLMTruncated as e:
            return {"ok": False, "data": None, "error": str(e), "attempts": attempt + 1}
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
        if attempt < max_retries and raw:      # 把坏输出和错误原因喂回去,让模型重出
            messages = messages[:1] + [{"role": "assistant", "content": raw},
                                       {"role": "user", "content": "你上一轮的回复有问题(%s)。请重新输出严格的 JSON,不要任何额外文字,结构与要求一致。" % err}]
    return {"ok": False, "data": None, "error": err, "attempts": max_retries + 1}


def render_markdown(data, job_title=""):
    s = data.get("candidate_summary") or {}
    out = ["# 面试题:%s · %s" % (job_title or "目标岗位", s.get("name") or "候选人"), ""]
    out += ["- 路线 / 级别:%s · %s" % (s.get("path", "—"), s.get("level", "—")),
            "- 工龄:%s 年" % s.get("years_of_experience", "?"),
            "- 背景:%s" % s.get("background_summary", "—"), ""]
    if data.get("thinking"):
        out += ["**面试策略**:%s" % data["thinking"], ""]
    n = 0
    for cat in CATEGORIES:
        qs = [q for q in data.get("questions") or [] if q.get("category") == cat]
        if not qs:
            continue
        out += ["## %s题(%d 道)" % (cat, len(qs)), ""]
        for q in qs:
            n += 1
            out += ["### Q%d(%s)" % (n, q.get("difficulty", "—")), "", (q.get("title") or "").strip(), ""]
            if q.get("hints"):
                out.append("- 提示关键词:" + " / ".join(map(str, q["hints"])))
            if q.get("key_points"):
                out.append("- 答案要点:")
                out += ["  - " + str(p).lstrip("•· ").strip() for p in q["key_points"]]
            if q.get("evaluation_dimensions"):
                out.append("- 评估维度:" + " / ".join(map(str, q["evaluation_dimensions"])))
            if q.get("follow_up_if_good"):
                out.append("- 答得好,追问:" + str(q["follow_up_if_good"]))
            if q.get("follow_up_if_bad"):
                out.append("- 答得不好,追问:" + str(q["follow_up_if_bad"]))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def find_chinese_font():
    """找一个能显示中文的字体文件。也可以用环境变量 RESUME_SCREENER_FONT 指定。"""
    cands = [os.environ.get("RESUME_SCREENER_FONT") or ""]
    system = platform.system()
    if system == "Darwin":
        cands += ["/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/PingFang.ttc"]
    elif system == "Linux":
        cands += ["/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Regular.otf",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                  "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                  "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
                  "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"]
    else:
        cands += ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]
    for c in cands:
        if c and Path(c).exists():
            return c
    raise FileNotFoundError("没找到中文字体。请用环境变量 RESUME_SCREENER_FONT 指定一个字体文件(.ttf / .ttc / .otf)")


def _clean(text, max_len=2000):
    s = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029]", "", "" if text is None else str(text))
    return s[:max_len] + "..." if len(s) > max_len else s


def generate_interview_pdf(data, job_title="", font_path=None):
    """把面试题转成 PDF,返回 bytes。需要 fpdf2(pip install fpdf2)和一个中文字体。"""
    try:
        from fpdf import FPDF
    except ImportError:
        raise RuntimeError("导出 PDF 需要 fpdf2,请先安装: pip install fpdf2")
    if not data or not data.get("questions"):
        raise ValueError("没有可导出的题目")
    font = font_path or find_chinese_font()
    import logging
    logging.getLogger("fontTools").setLevel(logging.ERROR)      # 字体子集化时的无害提示,不用打出来
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    class PDF(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("CJK", "", 8)
            self.set_text_color(148, 163, 184)
            self.cell(0, 5, _clean("面试题 · 第 %d 页 / 共 {nb} 页 · 生成于 %s" % (self.page_no(), stamp)), align="C")

    pdf = PDF(orientation="P", unit="mm", format="A4")
    pdf.add_font("CJK", "", font)
    pdf.add_font("CJK", "B", font)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(left=15, top=18, right=15)
    pdf.alias_nb_pages()
    pdf.add_page()

    def line(txt, size=10.5, bold=False, color=(71, 85, 105), h=5.5, multi=False):
        pdf.set_font("CJK", "B" if bold else "", size)
        pdf.set_text_color(*color)
        if multi:
            pdf.multi_cell(0, h, _clean(txt), new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.cell(0, h, _clean(txt), new_x="LMARGIN", new_y="NEXT")

    s = data.get("candidate_summary") or {}
    line("面试题", 22, True, (15, 23, 42), 12)
    line("%s · %s" % (job_title or "目标岗位", s.get("name") or "候选人"), 12, False, (99, 102, 241), 7)
    pdf.ln(4)
    line("候选人画像", 14, True, (15, 23, 42), 8)
    for t in ("路线 / 级别:%s · %s" % (s.get("path", "—"), s.get("level", "—")),
              "工龄:%s 年" % s.get("years_of_experience", "?"), "背景:%s" % s.get("background_summary", "—")):
        line(t, 11, h=6, multi=True)
    if data.get("thinking"):
        pdf.ln(2)
        line("面试策略", 12, True, (139, 92, 246), 7)
        line(data["thinking"], multi=True)
    pdf.ln(4)
    colors = {"技术": (99, 102, 241), "行为": (245, 158, 11), "反问": (236, 72, 153)}
    n = 0
    for cat in CATEGORIES:
        qs = [q for q in data["questions"] if q.get("category") == cat]
        if not qs:
            continue
        line("%s题(%d 道)" % (cat, len(qs)), 13, True, colors[cat], 8)
        for q in qs:
            n += 1
            line("Q%d / %s" % (n, q.get("difficulty", "—")), 10, True, colors[cat], 6)
            line((q.get("title") or "").strip(), 11.5, True, (15, 23, 42), 6, multi=True)
            for label, val in (("提示关键词", " / ".join(map(str, q.get("hints") or []))),
                               ("答案要点", "\n".join("· " + str(p).lstrip("•· ").strip() for p in q.get("key_points") or [])),
                               ("评估维度", " / ".join(map(str, q.get("evaluation_dimensions") or []))),
                               ("答得好,追问", q.get("follow_up_if_good") or ""),
                               ("答得不好,追问", q.get("follow_up_if_bad") or "")):
                if val:
                    line("- " + label, 9.5, True, (148, 163, 184), 5)
                    line(val, 10, multi=True)
            pdf.ln(4)
    pdf.set_title(_clean("面试题 - %s" % (s.get("name") or "候选人")))
    out = pdf.output()
    return bytes(out) if not isinstance(out, str) else out.encode("latin-1", errors="replace")
