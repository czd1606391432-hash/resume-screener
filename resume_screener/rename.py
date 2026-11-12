"""姓名兜底识别和统一命名。姓名优先用模型解析的结果,模型没抽到(或快筛阶段还没调模型)时用这里的规则。"""
import re
from datetime import datetime

_NOT_NAMES = {"简历", "个人", "姓名", "求职", "应聘", "基本"}


def guess_name_from_text(text):
    head = text[:3000]
    patterns = [
        r"姓\s*名\s*[：:\s]\s*([一-龥·]{2,5})",
        r"Name[ ]*[：:][ ]*([A-Z][a-z]+(?:[ ]+[A-Z][a-z]+)*)",
        r"^\s*([一-龥]{2,4})\s*(?:的)?(?:个人简历|简历)",
        r"^\s*([一-龥]{2,4})\s*[|｜/,，\s]\s*(?:男|女)\b",
    ]
    for p in patterns:
        m = re.search(p, head, re.MULTILINE)
        if m and m.group(1).strip() not in _NOT_NAMES:
            return m.group(1).strip()
    return ""


def name_from_pdf_meta(path):
    """PDF 属性里的作者,有时就是候选人姓名。没装 pypdf 就跳过。"""
    try:
        from pypdf import PdfReader
        author = ((PdfReader(str(path)).metadata or {}).get("/Author") or "").strip()
    except Exception:
        return ""
    if author and "@" not in author and not re.match(r"^[\d\-+\s]+$", author) and len(author) <= 20:
        return author
    return ""


def safe_name(name):
    """去掉不能出现在文件名里的字符。"""
    return re.sub(r'[\\/:*?"<>|\r\n\t]', "", (name or "").strip())[:40]


def new_filename(name, fmt="{name}-简历-{ts}", ts_pattern="%Y%m%d-%H%M%S", now=None):
    return fmt.format(name=safe_name(name) or "未知", ts=(now or datetime.now()).strftime(ts_pattern))
