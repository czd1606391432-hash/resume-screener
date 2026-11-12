"""简历文本提取:PDF / DOCX / TXT → 纯文本。

两件值得一提的事:
1. PDF 平铺水印剔除。招聘平台导出的简历常带逐字平铺的水印,抽文本时会混进正文,
   把姓名、学历劈开。这里按版面位置识别水印字符并在抽取前剔除。
2. 解析不可信文件的资源上限。几 KB 的特制文件就能让解析库吃掉几 GB 内存,
   所以在 Linux 上默认放进受限子进程里解析,超限只死子进程。
"""
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

SANDBOX_MEM_MB = int(os.environ.get("RESUME_SCREENER_MEM_MB", "600"))
SANDBOX_CPU_S = 25
SANDBOX_TIMEOUT_S = 40
MAX_PDF_PAGES = 60
MAX_TEXT_CHARS = 400_000
MAX_DOCX_XML = 6 * 1024 * 1024          # docx 里 xml 部分解压后的总量上限
MAX_DOCX_ALL = 80 * 1024 * 1024         # docx 整包解压后的总量上限
SUPPORTED = (".pdf", ".docx", ".txt")
_SANDBOX_SEM = threading.BoundedSemaphore(2)


def watermark_keys(page, min_repeat=3, x_bucket=5, cv_max=0.35):
    """找出这一页里平铺水印字符的定位键 {(x0, top, 字)}。

    不能用字符黑名单:水印里的字(比如公司名)恰恰也会出现在正文里,必须按版面位置判定。

    主判据(三条同时满足):
      1. 单字 token(水印是逐字平铺的)
      2. 同一个字在同一 x 列重复至少 3 次
      3. 这些出现的 y 间距高度均匀,并且纵向铺满半页以上
         (平铺水印是等距的;正文的小节标题随内容长短排布,间距不齐)
    另外两条:
      A. x 出界(小于 0 或超过页宽)的字符一定是水印
      C. 用已确认的水印字符反推「字 + 字号 + 字体」,补齐零散的漏网字符
    """
    import collections
    import statistics

    keys = set()
    W, H = page.width, page.height
    for c in page.chars:                                  # A) 出界字符
        if c["x0"] < 0 or c["x1"] > W:
            keys.add((round(c["x0"]), round(c["top"]), c["text"]))
    cols = collections.defaultdict(list)                  # B) 同字同列 + 等距铺开
    for w in page.extract_words():
        if len(w["text"]) == 1:
            cols[(w["text"], round(w["x0"] / x_bucket) * x_bucket)].append(w)
    for (ch, _xb), ws in cols.items():
        if len(ws) < min_repeat:
            continue
        ys = sorted(round(w["top"]) for w in ws)
        diffs = [b - a for a, b in zip(ys, ys[1:])]
        if not diffs:
            continue
        mean = statistics.fmean(diffs)
        if mean <= 0:
            continue
        cv = (statistics.pstdev(diffs) / mean) if len(diffs) > 1 else 0.0
        if cv <= cv_max and (ys[-1] - ys[0]) > H * 0.5:
            for w in ws:
                keys.add((round(w["x0"]), round(w["top"]), ch))
    if keys:                                              # C) 按签名补漏
        sig, wm_chars = set(), set()
        for c in page.chars:
            if (round(c["x0"]), round(c["top"]), c["text"]) in keys:
                sig.add((round(c["size"], 1), c.get("fontname")))
                wm_chars.add(c["text"])
        for c in page.chars:
            if c["text"] in wm_chars and (round(c["size"], 1), c.get("fontname")) in sig:
                keys.add((round(c["x0"]), round(c["top"]), c["text"]))
    return keys


def extract_pdf(path):
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("缺少 pdfplumber,请先安装: pip install pdfplumber")
    chunks = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages):
            if i >= MAX_PDF_PAGES or sum(len(c) for c in chunks) > MAX_TEXT_CHARS:
                break
            try:
                keys = watermark_keys(page)
            except Exception:
                keys = set()          # 识别失败就退回普通抽取,绝不因此丢简历
            if keys:
                # 先剔除水印字符,再走版面引擎,被水印行劈开的内容才能重新合并
                def keep(obj, _k=keys):
                    if obj.get("object_type") != "char":
                        return True
                    return (round(obj["x0"]), round(obj["top"]), obj.get("text")) not in _k
                t = page.filter(keep).extract_text() or ""
            else:
                t = page.extract_text() or ""
            if t.strip():
                chunks.append(t)
    return "\n".join(chunks)


def extract_docx(path):
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError("缺少 python-docx,请先安装: pip install python-docx")
    import zipfile
    with zipfile.ZipFile(str(path)) as z:      # 解压炸弹闸门
        inf = z.infolist()
        if (sum(max(0, i.file_size) for i in inf if i.filename.lower().endswith((".xml", ".rels"))) > MAX_DOCX_XML
                or sum(max(0, i.file_size) for i in inf) > MAX_DOCX_ALL):
            raise ValueError("文档体积异常")
    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


def _extract_inproc(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".txt":
        return Path(path).read_text(encoding="utf-8", errors="replace")[:MAX_TEXT_CHARS]
    if suffix == ".doc":
        raise NotImplementedError(".doc 暂不支持,请先另存为 .docx")
    raise NotImplementedError("格式不支持: %s" % suffix)


def use_sandbox():
    """默认只在 Linux 上启用子进程沙箱(内存上限依赖 Linux 的 RLIMIT_AS)。
    环境变量 RESUME_SCREENER_SANDBOX=1 / 0 可以强制开 / 关。"""
    flag = os.environ.get("RESUME_SCREENER_SANDBOX")
    if flag in ("0", "1"):
        return flag == "1"
    return sys.platform.startswith("linux") and not getattr(sys, "frozen", False)


def extract(path):
    """抽文本。启用沙箱时,每次解析起一个子进程,子进程自设内存 / CPU 上限;同时最多 2 个并发。"""
    path = Path(path)
    if not use_sandbox() or path.suffix.lower() not in (".pdf", ".docx"):
        return _extract_inproc(path)
    if not _SANDBOX_SEM.acquire(timeout=45):
        raise RuntimeError("当前解析任务较多,请稍后再试")
    try:
        env = dict(os.environ)
        pkg_parent = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = pkg_parent + os.pathsep + env.get("PYTHONPATH", "")
        try:
            r = subprocess.run([sys.executable, "-m", "resume_screener.extract", "--worker", str(path)],
                               capture_output=True, timeout=SANDBOX_TIMEOUT_S, start_new_session=True, env=env)
        except subprocess.TimeoutExpired:
            raise RuntimeError("文件解析超时(文件过大或格式异常)")
    finally:
        _SANDBOX_SEM.release()
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("文件解析失败(文件过大或格式异常)")
    d = json.loads(r.stdout.decode("utf-8", "replace"))
    if not d.get("ok"):
        raise RuntimeError(str(d.get("err") or "文件解析失败")[:300])
    return d.get("text") or ""


def _worker_main(path):
    try:
        import resource
        lim = SANDBOX_MEM_MB * 1024 * 1024
        for res, val in ((resource.RLIMIT_AS, (lim, lim)), (resource.RLIMIT_CPU, (SANDBOX_CPU_S, SANDBOX_CPU_S + 2))):
            try:
                resource.setrlimit(res, val)
            except (ValueError, OSError):
                pass          # 有的系统(如 macOS)不支持限制地址空间
        os.nice(5)
    except (ImportError, OSError):
        pass
    try:
        out = {"ok": True, "text": (_extract_inproc(Path(path)) or "")[:MAX_TEXT_CHARS]}
    except BaseException as e:      # 含 MemoryError
        out = {"ok": False, "err": "%s: %s" % (type(e).__name__, str(e)[:200])}
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


def normalize(t):
    """压掉多余空白,但保留换行(教育 / 工作经历是分行的)。"""
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        _worker_main(sys.argv[2])
        sys.exit(0)
    if len(sys.argv) < 2:
        print("用法: python -m resume_screener.extract <文件路径>")
        sys.exit(1)
    print(normalize(extract(Path(sys.argv[1]))))
