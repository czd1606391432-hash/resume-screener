"""汇总表:通过甄选的候选人,一人一行,最后一列是指向简历文件的超链接。"""
import threading
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SHEET = "候选人"
RESUME_COL = "简历"
HEADERS = ["序号", "姓名", "性别", "年龄", "电话", "邮箱", "所在地", "最高学历", "学校", "专业",
           "工作年限", "简历关键字", "最近两段工作80字总结", RESUME_COL]
COL_WIDTHS = {"序号": 6, "姓名": 12, "性别": 6, "年龄": 6, "电话": 16, "邮箱": 24, "所在地": 10, "最高学历": 10,
              "学校": 24, "专业": 18, "工作年限": 9, "简历关键字": 40, "最近两段工作80字总结": 60, RESUME_COL: 16}
_LOCK = threading.Lock()      # 多线程同时追加会写坏 xlsx


def _open(xlsx_path):
    if Path(xlsx_path).exists():
        wb = load_workbook(str(xlsx_path))
        if SHEET in wb.sheetnames:
            return wb
        ws = wb.create_sheet(SHEET)
    else:
        wb = Workbook()
        wb.remove(wb.active)
        ws = wb.create_sheet(SHEET)
    for ci, h in enumerate(HEADERS, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.fill = PatternFill(start_color="305496", end_color="305496", fill_type="solid")
        cell.font = Font(color="FFFFFF", bold=True, size=11)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(ci)].width = COL_WIDTHS[h]
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"
    return wb


def append_candidate(xlsx_path, info, resume_path):
    """追加一行,返回序号。按表头名找列,所以表里的列被人调过顺序、加过列也不会写错位。"""
    with _LOCK:
        xlsx_path = Path(xlsx_path)
        wb = _open(xlsx_path)
        ws = wb[SHEET]
        cols = {(c.value or ""): i for i, c in enumerate(ws[1], 1)}
        row = ws.max_row + 1
        seq = row - 1
        values = {
            "序号": seq, "姓名": info.get("name") or "", "性别": info.get("gender") or "",
            "年龄": info.get("age") or "", "电话": info.get("phone") or "", "邮箱": info.get("email") or "",
            "所在地": info.get("current_location") or "", "最高学历": info.get("highest_degree") or "",
            "学校": info.get("highest_school") or "", "专业": info.get("highest_major") or "",
            "工作年限": info.get("total_work_years") if info.get("total_work_years") is not None else "",
            "简历关键字": ", ".join(str(k) for k in info.get("keywords") or []),
            "最近两段工作80字总结": info.get("last_two_jobs_summary") or "",
        }
        for name, val in values.items():
            if name in cols:
                cell = ws.cell(row=row, column=cols[name], value=val)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                cell.font = Font(size=10)
        if RESUME_COL in cols:
            cell = ws.cell(row=row, column=cols[RESUME_COL], value="查看简历")
            cell.hyperlink = str(Path(resume_path).absolute())
            cell.font = Font(color="0563C1", underline="single", size=10)
            cell.alignment = Alignment(vertical="top", horizontal="center")
        ws.row_dimensions[row].height = 60
        xlsx_path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(str(xlsx_path))
        return seq


def load_candidates(xlsx_path):
    """读回汇总表里的全部候选人(JD 反选用)。"""
    if not Path(xlsx_path).exists():
        return []
    ws = load_workbook(str(xlsx_path))[SHEET]
    cols = {(c.value or ""): i for i, c in enumerate(ws[1])}

    def get(row, name, default=""):
        i = cols.get(name)
        return row[i].value if i is not None and row[i].value is not None else default

    out = []
    for row in ws.iter_rows(min_row=2):
        if not any(c.value for c in row):
            continue
        link = row[cols[RESUME_COL]] if RESUME_COL in cols else None
        out.append({
            "seq": get(row, "序号", None), "name": get(row, "姓名"), "gender": get(row, "性别"),
            "age": get(row, "年龄", None), "phone": get(row, "电话"), "email": get(row, "邮箱"),
            "city": get(row, "所在地"), "degree": get(row, "最高学历"), "school": get(row, "学校"),
            "major": get(row, "专业"), "exp_years": get(row, "工作年限", None),
            "keywords": [k.strip() for k in str(get(row, "简历关键字")).split(",") if k.strip()],
            "summary": str(get(row, "最近两段工作80字总结")),
            "resume_path": (link.hyperlink.target if link is not None and link.hyperlink else ""),
        })
    return out
