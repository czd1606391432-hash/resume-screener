"""JD 反选:给一段岗位要求,从汇总表里的候选人中挑出最匹配的。

两步:
  1. 模型读 JD,分出【硬性条件】(不符合就淘汰)和【软性关键词】(用来打分排序)
  2. 硬性条件逐条过滤 → 剩下的人按关键词打分 → 取前 N 名
候选人某一项信息未知(比如没写所在地、工作年限)时默认放行,不因为「不知道」而淘汰。
"""
import json
import re

from . import llm
from .summary import load_candidates

JD_EXTRACT_PROMPT = """你是招聘助手。请仔细阅读下面的岗位要求(JD),区分【硬性条件】和【软性关键词】。

【硬性条件】不符合就直接淘汰:
- 学历下限:博士 / 硕士 / 本科 / 专科 / 不限
- 经验年限下限(整数,没写就 null)
- 工作地点(数组,照抄 JD 原文里的省或市,如 ["苏州", "湖南"];没写、「不限地点」「全国」都是 [])
- 性别:男 / 女 / 不限(JD 没写就是「不限」)
- 年龄下限、年龄上限(整数,没写就 null)

【软性关键词】用来打分排序,不直接淘汰:
- 技能 / 工具 / 证书 / 行业 / 职位 / 专业,用简历里常见的写法,10 个左右
- 「年轻」「有冲劲」这类模糊诉求不要写进硬性条件,也不用写进关键词

只输出 JSON,不要任何其它文字,不要 markdown:

{{
  "thinking": "逐句分析 JD:哪些是硬性条件,哪些是软性要求(两三句话)",
  "hard": {{
    "degree_min": "博士" | "硕士" | "本科" | "专科" | "不限",
    "experience_min": int | null,
    "locations": [str],
    "gender": "男" | "女" | "不限",
    "age_min": int | null,
    "age_max": int | null
  }},
  "soft_keywords": [str]
}}

JD:
{jd_text}
"""


def default_hard():
    return {"degree_min": "不限", "experience_min": None, "locations": [],
            "gender": "不限", "age_min": None, "age_max": None}


def _int(v):
    try:
        return int(v) if v not in (None, "", "未知") else None
    except (ValueError, TypeError):
        return None


def parse_json_lenient(raw):
    """从模型回复里取 JSON,容忍代码块包裹、前后多余文字。取不出来返回 None。"""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    s = raw.find("{")
    if s >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw[s:])
            return obj
        except Exception:
            pass
    return None


def extract_jd_requirements(jd_text, chat=None):
    """JD → {"thinking", "hard", "soft_keywords"}。模型回复解析不出 JSON 时退一步:不设硬性条件,只把回复拆成关键词。"""
    chat = chat or llm.chat
    raw = chat([{"role": "user", "content": JD_EXTRACT_PROMPT.format(jd_text=jd_text)}], max_tokens=4000)
    parsed = parse_json_lenient(raw)
    if not isinstance(parsed, dict):
        parts = re.split(r"[\n,、;;\s]+", raw)
        return {"thinking": "(模型回复不是合法 JSON,只取了关键词,没有硬性条件)", "hard": default_hard(),
                "soft_keywords": [p.strip() for p in parts if 2 <= len(p.strip()) <= 20][:15]}
    h = parsed.get("hard") or {}
    hard = default_hard()
    hard.update({
        "degree_min": h.get("degree_min") or "不限",
        "experience_min": _int(h.get("experience_min")),
        "locations": [str(x).strip() for x in (h.get("locations") or h.get("location_provinces") or []) if str(x).strip()],
        "gender": h.get("gender") if h.get("gender") in ("男", "女") else "不限",
        "age_min": _int(h.get("age_min")), "age_max": _int(h.get("age_max")),
    })
    return {"thinking": str(parsed.get("thinking") or ""), "hard": hard,
            "soft_keywords": [str(k).strip() for k in (parsed.get("soft_keywords") or []) if str(k).strip()][:15]}


# 省 → 地级市。JD 写省、简历写市时靠它对上。不求穷尽,漏了的后果是按省查时误拒那个市,发现了补上即可。
PROVINCE_CITIES = {
    "北京": ["北京"], "上海": ["上海"], "天津": ["天津"], "重庆": ["重庆"],
    "河北": ["石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德", "沧州", "廊坊", "衡水"],
    "山西": ["太原", "大同", "阳泉", "长治", "晋城", "朔州", "晋中", "运城", "忻州", "临汾", "吕梁"],
    "内蒙古": ["呼和浩特", "包头", "乌海", "赤峰", "通辽", "鄂尔多斯", "呼伦贝尔", "巴彦淖尔", "乌兰察布"],
    "辽宁": ["沈阳", "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州", "营口", "阜新", "辽阳", "盘锦", "铁岭", "朝阳", "葫芦岛"],
    "吉林": ["长春", "吉林", "四平", "辽源", "通化", "白山", "松原", "白城", "延边"],
    "黑龙江": ["哈尔滨", "齐齐哈尔", "鸡西", "鹤岗", "双鸭山", "大庆", "伊春", "佳木斯", "七台河", "牡丹江", "黑河", "绥化"],
    "江苏": ["南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁", "昆山", "江阴", "张家港", "常熟", "太仓"],
    "浙江": ["杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水", "义乌"],
    "安徽": ["合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州", "阜阳", "宿州", "六安", "亳州", "池州", "宣城"],
    "福建": ["福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩", "宁德"],
    "江西": ["南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶"],
    "山东": ["济南", "青岛", "淄博", "枣庄", "东营", "烟台", "潍坊", "济宁", "泰安", "威海", "日照", "临沂", "德州", "聊城", "滨州", "菏泽"],
    "河南": ["郑州", "开封", "洛阳", "平顶山", "安阳", "鹤壁", "新乡", "焦作", "濮阳", "许昌", "漯河", "三门峡", "南阳", "商丘", "信阳", "周口", "驻马店"],
    "湖北": ["武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州", "恩施"],
    "湖南": ["长沙", "株洲", "湘潭", "衡阳", "邵阳", "岳阳", "常德", "张家界", "益阳", "郴州", "永州", "怀化", "娄底", "湘西"],
    "广东": ["广州", "深圳", "珠海", "汕头", "佛山", "韶关", "湛江", "肇庆", "江门", "茂名", "惠州", "梅州", "汕尾", "河源", "阳江", "清远", "东莞", "中山", "潮州", "揭阳", "云浮"],
    "广西": ["南宁", "柳州", "桂林", "梧州", "北海", "防城港", "钦州", "贵港", "玉林", "百色", "贺州", "河池", "来宾", "崇左"],
    "海南": ["海口", "三亚", "三沙", "儋州"],
    "四川": ["成都", "自贡", "攀枝花", "泸州", "德阳", "绵阳", "广元", "遂宁", "内江", "乐山", "南充", "眉山", "宜宾", "广安", "达州", "雅安", "巴中", "资阳", "凉山"],
    "贵州": ["贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁", "黔东南", "黔南", "黔西南"],
    "云南": ["昆明", "曲靖", "玉溪", "保山", "昭通", "丽江", "普洱", "临沧", "大理", "红河", "楚雄", "西双版纳"],
    "西藏": ["拉萨", "日喀则", "昌都", "林芝", "山南", "那曲"],
    "陕西": ["西安", "铜川", "宝鸡", "咸阳", "渭南", "延安", "汉中", "榆林", "安康", "商洛"],
    "甘肃": ["兰州", "嘉峪关", "金昌", "白银", "天水", "武威", "张掖", "平凉", "酒泉", "庆阳", "定西", "陇南"],
    "青海": ["西宁", "海东"],
    "宁夏": ["银川", "石嘴山", "吴忠", "固原", "中卫"],
    "新疆": ["乌鲁木齐", "克拉玛依", "吐鲁番", "哈密", "阿克苏", "喀什", "伊犁", "昌吉"],
}


def _strip_loc(s):
    return str(s or "").strip().replace("省", "").replace("市", "").replace("自治区", "")


def loc_match(cand_city, wanted):
    """JD 要求的地点(省或市)和候选人所在地是否对得上。所在地未知 → 放行。"""
    c = _strip_loc(cand_city)
    if not c:
        return True
    for w in wanted:
        w = _strip_loc(w)
        if not w:
            continue
        if w in c or c in w:
            return True
        if any(c.startswith(city) for city in PROVINCE_CITIES.get(w, [])):
            return True
    return False


DEGREE_ORDER = {"不限": 0, "专科": 1, "大专": 1, "本科": 2, "硕士": 3, "MBA": 3, "博士": 4}


def degree_level(s):
    """学历写法 → 等级。先全等,再按包含兜底(「硕士研究生」「博士后」)。"""
    s = (s or "").strip()
    if DEGREE_ORDER.get(s):
        return DEGREE_ORDER[s]
    for k in ("博士", "硕士", "研究生", "本科", "学士", "大专", "专科", "MBA"):
        if k in s:
            return {"研究生": 3, "学士": 2}.get(k) or DEGREE_ORDER[k]
    return 0


def hard_filter(cand, hard, allow_gender=False, allow_age=False):
    """硬性过滤。返回 (是否通过, 原因, 是哪一项)。

    性别和年龄默认**不**作为筛选条件(allow_gender / allow_age 为 False 时直接忽略 JD 里的这两项),
    需要的话在 rules.json 的 match 段里显式打开。是否可以按这两项筛选,请先确认当地的就业法规。
    """
    if hard.get("degree_min") and hard["degree_min"] != "不限":
        c_degree = (cand.get("degree") or "").strip()
        if not c_degree:
            return False, "学历未知(要求 %s 及以上)" % hard["degree_min"], "degree"
        if degree_level(c_degree) < degree_level(hard["degree_min"]):
            return False, "学历 %s 低于要求的 %s" % (c_degree, hard["degree_min"]), "degree"
    need = hard.get("experience_min")
    c_exp = _int(cand.get("exp_years"))
    if need is not None and c_exp is not None and c_exp < need:      # 年限未知不拒
        return False, "工作年限 %d 年,低于要求的 %d 年" % (c_exp, need), "experience"
    wanted = hard.get("locations") or []
    if wanted and not loc_match(cand.get("city") or "", wanted):
        return False, "所在地 %s 不在 %s" % (cand.get("city"), "/".join(wanted)), "location"
    if allow_gender and hard.get("gender") in ("男", "女"):
        c_gender = (cand.get("gender") or "").strip()
        if c_gender and c_gender != hard["gender"]:      # 性别未知不拒
            return False, "性别不符", "gender"
    if allow_age:
        c_age = _int(cand.get("age"))
        if c_age is not None:
            if hard.get("age_min") is not None and c_age < hard["age_min"]:
                return False, "年龄 %d 小于下限 %d" % (c_age, hard["age_min"]), "age"
            if hard.get("age_max") is not None and c_age > hard["age_max"]:
                return False, "年龄 %d 大于上限 %d" % (c_age, hard["age_max"]), "age"
    return True, "ok", None


def ignored_conditions(hard, allow_gender=False, allow_age=False):
    """JD 里写了、但按当前设置没有用来筛选的条件(用于在结果里明示)。"""
    out = []
    if not allow_gender and hard.get("gender") in ("男", "女"):
        out.append("性别")
    if not allow_age and (hard.get("age_min") is not None or hard.get("age_max") is not None):
        out.append("年龄")
    return out


def soft_score(jd_keywords, cand):
    """软性打分,返回 (0~1 的分数, 命中的关键词)。

      JD 关键词被候选人关键词覆盖的比例        50%
      两边关键词的 Jaccard 相似度               30%
      JD 关键词出现在「最近两段工作总结」里的比例  20%
    关键词按不分大小写的全等来比。
    """
    jd = {k.strip().lower() for k in jd_keywords if k.strip()}
    if not jd:
        return 0.0, []
    ck = {k.strip().lower() for k in cand.get("keywords") or [] if k.strip()}
    hit = ck & jd
    coverage = len(hit) / len(jd)
    jaccard = len(hit) / len(ck | jd)
    summary = (cand.get("summary") or "").lower()
    in_summary = sum(1 for k in jd if k in summary) / len(jd)
    return coverage * 0.5 + jaccard * 0.3 + in_summary * 0.2, sorted(hit)


def match(jd_text, candidates, top=5, allow_gender=False, allow_age=False, include_failed=False, chat=None):
    """完整跑一遍反选,返回一个可以直接转 JSON 的 dict。"""
    req = extract_jd_requirements(jd_text, chat=chat)
    hard, kws = req["hard"], req["soft_keywords"]
    passed, failed = [], []
    for c in candidates:
        ok, why, field = hard_filter(c, hard, allow_gender, allow_age)
        if ok:
            score, common = soft_score(kws, c)
            passed.append(dict(c, score=round(score, 4), common=common))
        else:
            failed.append(dict(c, score=None, common=[], fail_reason=why, fail_field=field))
    passed.sort(key=lambda x: -x["score"])
    return {"thinking": req["thinking"], "hard": hard, "soft_keywords": kws,
            "ignored": ignored_conditions(hard, allow_gender, allow_age),
            "total": len(candidates), "passed_count": len(passed), "failed_count": len(failed),
            "candidates": passed[:top], "failed": failed if include_failed else []}


def match_workspace(ws, jd_text, top=5, include_failed=False, chat=None):
    m = ws.rules.get("match") or {}
    return match(jd_text, load_candidates(ws.summary_xlsx), top, bool(m.get("allow_gender_filter")),
                 bool(m.get("allow_age_filter")), include_failed, chat)
