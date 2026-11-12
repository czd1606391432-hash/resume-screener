"""筛选规则的测试:学历语义、院校匹配、公司黑名单。不联网。运行: python3 -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from resume_screener import filter as flt          # noqa: E402
from resume_screener.config import default_rules   # noqa: E402

POOL = {"北京大学": "国内", "清华大学": "国内", "中南大学": "国内", "浙江大学": "国内", "华中科技大学": "国内",
        "广东工业大学": "国内", "澳大利亚国立大学": "海外", "麻省理工学院": "海外", "北京航空航天大学": "国内",
        "中国科学技术大学": "国内", "伦敦大学学院": "海外"}


def info(**kw):
    base = {"name": "张三", "highest_degree": "本科", "highest_school": "清华大学", "is_full_time": None,
            "is_zhengshou": None, "is_zhuan_sheng_ben": None, "work_history": []}
    base.update(kw)
    return base


class SchoolMatchTest(unittest.TestCase):
    def ok(self, name, aliases=None):
        return flt.school_in_pool(name, POOL, aliases)

    def test_exact_and_builtin_alias(self):
        self.assertTrue(self.ok("清华大学"))
        self.assertTrue(self.ok(" 北大 "))
        self.assertTrue(self.ok("北航"))
        self.assertTrue(self.ok("中科大"))
        self.assertTrue(self.ok("UCL"))
        self.assertFalse(self.ok("复旦"))                 # 简称认得,但复旦大学不在这份清单里
        self.assertFalse(self.ok(""))
        self.assertFalse(self.ok(None))

    def test_bracket_notes(self):
        self.assertTrue(self.ok("麻省理工学院(美国)"))
        self.assertTrue(self.ok("浙江大学（玉泉校区）"))

    def test_chinese_english_mixed(self):
        self.assertTrue(self.ok("澳大利亚国立大学Australian National University"))
        self.assertTrue(self.ok("麻省理工学院 MIT"))

    def test_custom_aliases(self):
        ali = {"massachusetts institute of technology": "麻省理工学院", "bad school": "不存在大学"}
        self.assertTrue(self.ok("Massachusetts Institute of Technology", ali))
        self.assertFalse(self.ok("Massachusetts Institute of Technology"))
        self.assertFalse(self.ok("Bad School", ali))

    def test_discipline_college_counts_as_parent(self):
        self.assertTrue(self.ok("华中科技大学同济医学院"))          # ≥5 字校名 + 学科类学院
        self.assertTrue(self.ok("华中科技大学管理学院"))
        self.assertTrue(self.ok("北京大学光华管理学院"))            # 4 字校名 + 学科类学院
        self.assertTrue(self.ok("中南大学湘雅医学院"))
        self.assertTrue(self.ok("北京大学法学院"))                  # 单字学科整词出现
        self.assertTrue(self.ok("北京大学深圳研究生院"))
        self.assertTrue(self.ok("华中科技大学人文学院"))

    def test_independent_college_rejected(self):
        self.assertFalse(self.ok("广东工业大学华立学院"))           # 独立学院不能冒充母体
        self.assertFalse(self.ok("华中科技大学附属中学"))
        self.assertTrue(self.ok("华中科技大学主校区"))               # 校区 / 分校这类后缀按本校算(见 README 的已知局限)
        self.assertFalse(self.ok("华中科技大学文华学院"))
        self.assertFalse(self.ok("北京大学继续教育学院"))
        self.assertFalse(self.ok("华中科技大学网络教育学院"))
        self.assertFalse(self.ok("浙江大学城市学院"))               # 4 字校名 + 非学科学院
        self.assertFalse(self.ok("浙江大学宁波理工学院"))
        self.assertFalse(self.ok("华中科技大学工商学院"))

    def test_similar_names_do_not_match(self):
        self.assertFalse(self.ok("北京大学医院"))
        self.assertFalse(self.ok("中南财经政法大学"))               # 不是「中南大学」
        self.assertFalse(self.ok("清华"[:1]))
        self.assertFalse(self.ok("南京大学"))

    def test_short_form_of_long_name(self):
        self.assertTrue(self.ok("北京航空航天"))                     # 清单里的校名以它开头,且写法够长
        self.assertFalse(self.ok("北京"))

    def test_load_pool_csv_and_xlsx(self):
        d = tempfile.mkdtemp()
        csv_p = os.path.join(d, "s.csv")
        with open(csv_p, "w", encoding="utf-8") as fh:
            fh.write("# 注释\n校名,分组\n甲大学,一类\n乙大学,二类\n\n丙学院\n")
        self.assertEqual(flt.load_school_pool(csv_p), {"甲大学": "一类", "乙大学": "二类", "丙学院": ""})
        self.assertEqual(flt.load_school_pool(csv_p, groups=["二类"]), {"乙大学": "二类"})
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "一类"
        for r in (["序号", "院校名称"], [1, "甲大学"], [2, "乙大学"]):
            ws.append(r)
        ws2 = wb.create_sheet("二类")
        for r in (["序号", "院校名称"], [1, "丁大学"]):
            ws2.append(r)
        xp = os.path.join(d, "s.xlsx")
        wb.save(xp)
        self.assertEqual(flt.load_school_pool(xp, xlsx_name_column=2), {"甲大学": "一类", "乙大学": "一类", "丁大学": "二类"})
        self.assertEqual(flt.load_school_pool(xp, groups=["二类"], xlsx_name_column=2), {"丁大学": "二类"})

    def test_bundled_example_list_loads(self):
        from resume_screener.config import DATA
        pool = flt.load_school_pool(DATA / "schools.example.csv")
        self.assertIn("清华大学", pool)
        self.assertGreater(len(pool), 30)
        self.assertNotIn("校名", pool)
        self.assertTrue(flt.load_aliases(DATA / "school_aliases.example.json"))
        self.assertEqual(flt.load_aliases(os.path.join(tempfile.mkdtemp(), "none.json")), {})


class EducationRuleTest(unittest.TestCase):
    def setUp(self):
        self.rules = default_rules()

    def quick(self, text):
        return flt.quick_filter(text, self.rules)

    def test_degree_needs_context(self):
        self.assertFalse(self.quick("姓名:张三\n最高学历:大专\n工作经历\n……")[0])
        self.assertFalse(self.quick("张三,2015 年大专毕业,从事销售")[0])
        self.assertFalse(self.quick("教育经历\n2012.09-2015.06 示例职业技术学院 机电一体化 专科\n\n工作经历\n略")[0])
        # 下面这些都只是「提到」了专科 / 高职,不该淘汰
        self.assertTrue(self.quick("负责招聘,岗位要求大专及以上学历")[0])
        self.assertTrue(self.quick("曾在高职院校授课三年\n教育经历\n2010.09-2014.06 示例大学 教育学 本科\n\n工作经历")[0])
        self.assertTrue(self.quick("教育经历\n2010.09-2013.06 示例学院 专科\n2013.09-2016.06 示例大学 本科\n\n工作经历")[0])

    def test_exclude_keywords_only_in_education_block(self):
        bad = "教育经历\n2014.03-2017.01 示例大学 工商管理 本科(函授)\n\n工作经历\n销售主管"
        ok, why = self.quick(bad)
        self.assertFalse(ok)
        self.assertIn("函授", why)
        # 工作内容里提到这些词,不算
        good = "教育经历\n2010.09-2014.06 示例大学 人力资源管理 本科\n\n工作经历\n负责非全日制用工管理,对接函授站和自考助学机构"
        self.assertTrue(self.quick(good)[0])
        # 截不到教育经历段:不在快筛里判,留给模型
        self.assertTrue(self.quick("张三,专升本后一直从事销售工作,业绩突出")[0])

    def test_rules_can_be_switched_off(self):
        self.rules["education"]["block_associate"] = False
        self.rules["education"]["exclude_keywords"] = []
        self.assertTrue(self.quick("最高学历:大专\n教育经历\n2012.09-2015.06 示例学院 专科 函授\n\n工作经历")[0])

    def test_unknown_defaults_to_pass(self):
        j = lambda **kw: flt.judge(info(**kw), self.rules, POOL)
        self.assertEqual(j(), (True, "ok"))                                    # 全日制 / 统招都没写 → 放行
        self.assertEqual(j(is_full_time=True, is_zhengshou=True), (True, "ok"))
        self.assertFalse(j(is_full_time=False)[0])                             # 明确非全日制才淘汰
        self.assertFalse(j(is_zhengshou=False)[0])
        self.assertFalse(j(is_zhuan_sheng_ben=True)[0])
        self.assertTrue(j(is_zhuan_sheng_ben=False)[0])
        self.assertFalse(j(highest_degree="专科")[0])
        self.assertTrue(j(highest_degree="硕士", highest_school="浙江大学")[0])

    def test_school_rule(self):
        j = lambda **kw: flt.judge(info(**kw), self.rules, POOL)
        ok, why = j(highest_school="示例学院")
        self.assertFalse(ok)
        self.assertIn("示例学院", why)
        self.assertEqual(j(highest_school=None), (False, "未识别到毕业院校"))
        self.rules["schools"]["require_in_list"] = False
        self.assertTrue(j(highest_school="示例学院")[0])
        self.assertTrue(j(highest_school=None)[0])

    def test_switches_in_judge(self):
        e = self.rules["education"]
        e["block_associate"] = e["reject_part_time"] = e["reject_upgrade"] = False
        self.assertTrue(flt.judge(info(highest_degree="专科", is_full_time=False, is_zhuan_sheng_ben=True), self.rules, POOL)[0])


class BlacklistTest(unittest.TestCase):
    def setUp(self):
        self.rules = default_rules()
        self.rules["company_blacklist"] = ["虚构重工"]

    def test_only_employment_counts(self):
        text = ("教育经历\n2012.09-2016.06 清华大学 计算机 本科\n\n工作经历\n"
                "2016.07-至今 样板软件有限公司\n负责与虚构重工的竞标分析,参与虚构重工项目交付")
        self.assertTrue(flt.quick_filter(text, self.rules)[0])                 # 只是提到,快筛不淘汰
        mention = info(work_history=[{"company": "样板软件有限公司", "responsibilities": ["参与虚构重工项目交付"]}])
        self.assertTrue(flt.judge(mention, self.rules, POOL)[0])
        worked = info(work_history=[{"company": "样板软件有限公司"}, {"company": "虚构重工有限公司"}])
        ok, why = flt.judge(worked, self.rules, POOL)
        self.assertFalse(ok)
        self.assertIn("虚构重工", why)

    def test_empty_blacklist_and_bad_data(self):
        self.rules["company_blacklist"] = []
        self.assertTrue(flt.judge(info(work_history=[{"company": "虚构重工有限公司"}]), self.rules, POOL)[0])
        self.rules["company_blacklist"] = ["虚构重工", ""]
        self.assertTrue(flt.judge(info(work_history=[None, {"company": None}, {}]), self.rules, POOL)[0])

    def test_default_blacklist_is_empty(self):
        self.assertEqual(default_rules()["company_blacklist"], [])


if __name__ == "__main__":
    unittest.main()
