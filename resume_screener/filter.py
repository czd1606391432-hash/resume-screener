"""筛选规则:学历类型、学历层级、院校清单、公司黑名单。

分两道闸:
  quick_filter  不调模型,只看简历原文里明确的信号,明显不合格的直接淘汰,省掉一次模型调用
  judge         基于模型解析出的结构化信息做最终判定
两道闸的共同原则:**简历没写清楚的,默认放行**,只有明确不符合才淘汰。
"""
import csv
import json
import re
from pathlib import Path

# 常见简称 → 全称。只是帮助匹配,合不合格仍然看全称在不在你的院校清单里。
SCHOOL_ALIASES = {
    "北大": "北京大学", "清华": "清华大学", "人大": "中国人民大学", "北航": "北京航空航天大学",
    "北理工": "北京理工大学", "北师大": "北京师范大学", "央财": "中央财经大学", "对外经贸": "对外经济贸易大学",
    "中传": "中国传媒大学", "复旦": "复旦大学", "上交": "上海交通大学", "上大": "上海大学",
    "华师": "华东师范大学", "同济": "同济大学", "南大": "南京大学", "东大": "东南大学", "浙大": "浙江大学",
    "中科大": "中国科学技术大学", "厦大": "厦门大学", "武大": "武汉大学", "华科": "华中科技大学",
    "西交": "西安交通大学", "哈工大": "哈尔滨工业大学", "西工大": "西北工业大学", "川大": "四川大学",
    "山大": "山东大学", "湖大": "湖南大学", "国防科大": "国防科技大学", "国防科学技术大学": "国防科技大学",
    "吉大": "吉林大学", "兰大": "兰州大学", "中大": "中山大学", "华南理工": "华南理工大学", "华工": "华南理工大学",
    "重大": "重庆大学", "深大": "深圳大学", "港大": "香港大学", "港中文": "香港中文大学", "港科大": "香港科技大学",
    "MIT": "麻省理工学院", "斯坦福": "斯坦福大学", "Stanford": "斯坦福大学", "Harvard": "哈佛大学",
    "Berkeley": "加州大学伯克利分校", "CMU": "卡内基梅隆大学", "Cambridge": "剑桥大学", "Oxford": "牛津大学",
    "UCL": "伦敦大学学院", "LSE": "伦敦政治经济学院", "ETH": "苏黎世联邦理工学院",
    "NTU": "南洋理工大学", "NUS": "新加坡国立大学", "早稻田": "早稻田大学",
}

_HEADER_WORDS = {"校名", "学校", "院校", "院校名称", "学校名称", "name", "school"}


def _paren(s):
    return (s or "").replace("（", "(").replace("）", ")")


def load_school_pool(path, groups=None, xlsx_name_column=1, xlsx_first_row=2):
    """读院校清单,返回 {校名: 分组}。

    .csv / .txt  每行「校名」或「校名,分组」;# 开头的行是注释;表头行自动跳过
    .xlsx        每个工作表是一个分组(分组名 = 表名);校名取第 xlsx_name_column 列,从第 xlsx_first_row 行开始
    groups 给了就只保留这些分组。
    """
    path = Path(path)
    pool = {}
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(min_row=xlsx_first_row, values_only=True):
                if row and len(row) >= xlsx_name_column and row[xlsx_name_column - 1]:
                    name = str(row[xlsx_name_column - 1]).strip()
                    if name and name.lower() not in _HEADER_WORDS:
                        pool.setdefault(name, ws.title)
    else:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.reader(fh):
                if not row or not row[0].strip() or row[0].lstrip().startswith("#"):
                    continue
                name = row[0].strip()
                if name.lower() in _HEADER_WORDS:
                    continue
                pool.setdefault(name, row[1].strip() if len(row) > 1 else "")
    if groups:
        pool = {k: v for k, v in pool.items() if v in set(groups)}
    return pool


def load_aliases(path):
    """读自定义校名别名 {别名: 清单里的校名}(比如外文名、旧校名)。文件不存在就返回空。"""
    try:
        p = Path(path)
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            return {str(k).strip().lower(): str(v).strip() for k, v in raw.items()
                    if v and not str(k).startswith("_")}
    except Exception:
        pass
    return {}


def normalize_school(name):
    n = _paren(name.strip())
    n = re.sub(r"^(教育部|国家|原)", "", n)
    if n in SCHOOL_ALIASES:
        return SCHOOL_ALIASES[n]
    n2 = re.sub(r"\([^)]*\)", "", n).strip()      # 去掉括号里的注记(国别 / 校区)再查一次
    return SCHOOL_ALIASES.get(n2, n2)


# 「某某大学 + 后缀」:后缀是学科类二级学院(管理学院 / 医学院…)才算本校;
# 「某某大学华立学院 / 城市学院」这类独立学院,以及继续教育 / 网络教育学院,不算。
_DISCIPLINE = re.compile(r"医|药|护理|管理|经济|经管|金融|会计|财经|信息|计算机|软件|电子|电气|机械|自动化|材料|化学|化工|"
                         r"土木|建筑|交通|航空|航天|能源|动力|环境|生命|生物|水利|海洋|地球|地质|测绘|数学|统计|物理|"
                         r"外国语|外语|语言|新闻|传播|传媒|艺术|音乐|美术|设计|教育|体育|历史|哲学|社会|政治|国际|马克思|"
                         r"公共|法学|法律|政法|文学|人文|理学|工学|工程|农学|林学|商学")
_DISCIPLINE_EXACT = {"商", "法", "工", "理", "文", "农", "林"}      # 单字学科必须整词出现:「文学院」算,「文华学院」不算
_NON_FULLTIME = re.compile(r"继续教育|网络教育|成人|远程|职业技术|夜大|函授|自考|在职|非全日制|培训")


def _clean_suffix(suffix):
    return re.sub(r"\([^)]*\)", "", _paren(suffix)).strip()


def _is_discipline(stem):
    return stem in _DISCIPLINE_EXACT or bool(_DISCIPLINE.search(stem))


def is_discipline_college(suffix):
    suf = _clean_suffix(suffix)
    if not suf or _NON_FULLTIME.search(suf):
        return False
    if suf.endswith(("研究生院", "研究院", "书院")):
        return True
    return suf.endswith("学院") and _is_discipline(suf[:-2])


def is_independent_suffix(suffix):
    suf = _clean_suffix(suffix)
    if not suf:
        return False
    if _NON_FULLTIME.search(suf) or re.search(r"附属|附中|中学|小学|幼儿园|医院$", suf):
        return True
    if suf.endswith("学院") or suf.endswith("分院"):
        return not _is_discipline(suf[:-2])
    return False


def school_in_pool(school, pool, aliases=None):
    """院校写法容错匹配,依次尝试:全等 → 内置简称 → 自定义别名 → 中英连写取中文部分 → 前缀互含。

    前缀互含的规则:
      清单里的校名以简历写法开头(简历写得更短),且简历写法至少 5 个字 → 算
      简历写法以清单校名开头(简历多写了二级学院):
        校名 ≥5 字:后缀不是独立学院 / 继续教育类 → 算
        校名 = 4 字:后缀必须是学科类二级学院 → 算(「北京大学光华管理学院」算,「××大学城市学院」不算)
    """
    if not school:
        return False
    school = school.strip()
    aliases = aliases or {}
    if school in pool:
        return True
    norm = normalize_school(school)
    if norm in pool:
        return True
    ali = aliases.get(school.lower()) or aliases.get(norm.lower())
    if ali and (ali in pool or normalize_school(ali) in pool):
        return True
    m = re.match(r"^([一-龥·]{4,})\s*[A-Za-z(]", _paren(school))      # 「××大学 Some University」→ 取中文部分
    if m and m.group(1) != school and school_in_pool(m.group(1), pool, aliases):
        return True
    if len(norm) >= 5:
        for p in pool:
            q = _clean_suffix(p)
            if len(q) < 4:
                continue
            if len(q) >= 5 and q.startswith(norm):
                return True
            if norm.startswith(q):
                suf = norm[len(q):]
                if len(q) >= 5 and not is_independent_suffix(suf):
                    return True
                if len(q) == 4 and is_discipline_college(suf):
                    return True
    return False


_SECTION = r"工作经历|工作经验|实习经历|项目经历|项目经验|自我评价|个人评价|专业技能|技能特长|获奖|证书"


def extract_education_block(text):
    """粗略截取「教育经历」这一段:从小节标题起,到下一个小节标题 / 空行 / 文末为止。截不到返回空串。"""
    for head in (r"教育经历|教育背景|学习经历", r"学历"):      # 先找明确的小节标题,找不到再退到「学历」二字
        m = re.search(r"(?:%s)[：:\s]*\n?(.{8,500}?)(?=\n\s*(?:%s)|\n\s*\n|\Z)" % (head, _SECTION), text, re.DOTALL)
        if m:
            return m.group(1)
    return ""


def check_degree_level(text, rules):
    """专科:必须带上下文才算。裸关键词会误杀「大专及以上」「在高职院校授课」。"""
    if not rules["education"].get("block_associate"):
        return True, "ok"
    if re.search(r"(最高学历|学历)[^\n]{0,6}(专科|大专)", text) or \
       re.search(r"(专科|大专)(毕业|学历)", text) or \
       re.search(r"(三年制|两年制).{0,4}(专科|大专)", text):
        return False, "学历为专科"
    edu = extract_education_block(text)
    if edu and not re.search(r"本科|学士|硕士|博士|研究生", edu) and re.search(r"专科|大专", edu):
        return False, "学历为专科"      # 教育经历里只有专科
    return True, "ok"


def quick_filter(text, rules):
    """快筛(不调模型)。返回 (是否通过, 原因)。

    公司黑名单不在这里查:对全文做子串匹配会误杀「负责与某公司的竞标分析」「参与某公司项目交付」,
    是否在那家公司任过职,要等模型解析出工作经历后再判(见 judge)。
    学历类型关键词只在「教育经历」这一段里找:全文里出现「函授」「非全日制」可能只是在描述工作内容;
    截不到教育经历段就不在这里判,留给模型。
    """
    edu = extract_education_block(text)
    if edu:
        for kw in rules["education"].get("exclude_keywords") or []:
            if kw in edu:
                return False, "学历类型不合格:教育经历含「%s」" % kw
    return check_degree_level(text, rules)


def judge(info, rules, pool, aliases=None):
    """基于模型解析结果的最终判定。返回 (是否通过, 原因)。"""
    edu = rules["education"]
    if edu.get("block_associate") and info.get("highest_degree") == "专科":
        return False, "学历为专科"
    if edu.get("reject_upgrade") and info.get("is_zhuan_sheng_ben") is True:
        return False, "学历为专升本"
    # 只有模型**明确判定**非全日制 / 非统招才淘汰;没写清楚(null)按全日制统招放行。
    # 大多数简历不写这两个词,「没认定就拒」会系统性误杀。
    if edu.get("reject_part_time"):
        if info.get("is_full_time") is False:
            return False, "明确标注非全日制"
        if info.get("is_zhengshou") is False:
            return False, "明确标注非统招"
    # 公司黑名单:只查工作经历里的公司字段
    for w in info.get("work_history") or []:
        company = (w or {}).get("company") or ""
        for blk in rules.get("company_blacklist") or []:
            if blk and blk in company:
                return False, "曾任职于黑名单公司:%s" % blk
    if (rules.get("schools") or {}).get("require_in_list"):
        school = info.get("highest_school")
        if not school:
            return False, "未识别到毕业院校"
        if not school_in_pool(school, pool, aliases):      # 只看最高学历的院校
            return False, "毕业院校「%s」不在院校清单" % school
    return True, "ok"
