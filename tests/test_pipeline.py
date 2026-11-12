"""文本提取、批处理全流程、命令行的测试。简历全部是虚构的,模型用假的替身,不联网。"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from examples import make_samples                                   # noqa: E402
from resume_screener import cli, extract, llm, pipeline, rename, summary   # noqa: E402
from resume_screener.config import ConfigError, init_workspace, load_workspace   # noqa: E402

# 假模型按简历里的姓名返回「解析结果」
PARSED = {
    "张三": {"name": "张三", "gender": None, "age": None, "phone": "000-0000-0001", "email": "zhangsan@example.com",
           "current_location": "长沙", "highest_degree": "本科", "highest_school": "清华大学", "highest_major": "计算机科学与技术",
           "is_full_time": True, "is_zhengshou": True, "is_zhuan_sheng_ben": False, "total_work_years": 9,
           "work_history": [{"period": "2019.07-至今", "company": "示例科技有限公司", "title": "高级后端工程师", "responsibilities": []},
                            {"period": "2016.07-2019.06", "company": "样板软件有限公司", "title": "后端工程师",
                             "responsibilities": ["参与与虚构重工的联合项目交付"]}],
           "keywords": ["Python", "Go", "微服务", "Kubernetes", "MySQL"], "last_two_jobs_summary": "负责订单系统的微服务拆分和支付网关开发"},
    "李四": {"name": "李四", "highest_degree": "本科", "highest_school": "示例学院", "is_full_time": None, "is_zhengshou": None,
           "is_zhuan_sheng_ben": None, "work_history": [{"company": "样板机械有限公司"}], "keywords": ["液压系统"]},
    "赵六": {"name": "赵六", "current_location": "上海", "highest_degree": "硕士", "highest_school": "浙江大学", "is_full_time": True,
           "is_zhengshou": True, "is_zhuan_sheng_ben": False, "total_work_years": 7,
           "work_history": [{"company": "虚构重工有限公司", "title": "结构工程师"}], "keywords": ["ANSYS", "结构设计"],
           "last_two_jobs_summary": "负责起重机臂架结构设计"},
}


class FakeModel:
    def __init__(self):
        self.seen = []

    def __call__(self, messages, system=None, max_tokens=None, timeout=None):
        text = messages[0]["content"]
        for name, parsed in PARSED.items():
            if name + " 个人简历" in text:
                self.seen.append(name)
                return json.dumps(parsed, ensure_ascii=False)
        self.seen.append("?")
        raise llm.LLMError("假模型不认识这份简历")


def make_workspace(blacklist=()):
    root = Path(tempfile.mkdtemp()) / "ws"
    ws, made = init_workspace(root)
    rules = json.loads((root / "rules.json").read_text(encoding="utf-8"))
    rules["company_blacklist"] = list(blacklist)
    (root / "rules.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return load_workspace(root), made


def quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


class ExtractTest(unittest.TestCase):
    def test_docx_paragraphs_and_tables(self):
        from docx import Document
        p = os.path.join(tempfile.mkdtemp(), "a.docx")
        doc = Document()
        doc.add_paragraph("张三   个人简历")
        t = doc.add_table(rows=1, cols=2)
        t.rows[0].cells[0].text, t.rows[0].cells[1].text = "学校", "清华大学"
        doc.save(p)
        text = extract.normalize(extract.extract(p))
        self.assertIn("张三 个人简历", text)          # 多余空白被压掉
        self.assertIn("清华大学", text)

    def test_txt_and_unsupported(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a.txt")
        Path(p).write_text("张三\r\n\r\n\r\n\r\n本科", encoding="utf-8")
        self.assertEqual(extract.normalize(extract.extract(p)), "张三\n\n本科")
        for name in ("a.doc", "a.png"):
            with self.assertRaises(NotImplementedError):
                extract.extract(os.path.join(d, name))

    def test_docx_zip_bomb_is_refused(self):
        p = os.path.join(tempfile.mkdtemp(), "bomb.docx")
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", b"0" * (extract.MAX_DOCX_XML + 1))
        self.assertLess(os.path.getsize(p), 100 * 1024)        # 压缩后很小,解压后超限
        with self.assertRaises(ValueError):
            extract.extract_docx(p)

    def test_pdf_watermark_removed(self):
        try:
            from fpdf import FPDF
            import pdfplumber  # noqa: F401
        except ImportError:
            self.skipTest("没装 fpdf2 或 pdfplumber,跳过 PDF 水印用例")
        pdf = FPDF(unit="pt", format="A4")
        pdf.set_auto_page_break(False)
        pdf.add_page()
        pdf.set_font("Helvetica", size=10)
        body = ["Wendy Zhang Resume", "Education: Example University, BSc Computer Science", "Work: Widget Works Ltd, Senior Engineer",
                "Skills: Python, Kubernetes, MySQL", "Managed a team of five and shipped the Q system"]
        for i, line in enumerate(body):
            pdf.text(60, 100 + i * 131, line)                # 正文行距特意和水印行距一致,让水印字落在正文行里
        pdf.set_font("Helvetica", size=16)
        for row in range(6):                                 # 平铺水印:W、Q 两个字各成一列,等距铺满整页
            for ch, x in (("W", 30), ("Q", 380), ("W", 520)):
                pdf.text(x, 100 + row * 131, ch)
        p = os.path.join(tempfile.mkdtemp(), "wm.pdf")
        pdf.output(p)
        import pdfplumber
        with pdfplumber.open(p) as doc:
            raw = doc.pages[0].extract_text()
            keys = extract.watermark_keys(doc.pages[0])
        self.assertEqual(raw.count("W Q W"), 6)               # 不处理的话,水印字混在正文里
        self.assertEqual(len(keys), 18)
        lines = extract.extract_pdf(p).split("\n")
        self.assertEqual(lines, body)                         # 水印没了;正文里的 W、Q 一个没少

    def test_pdf_without_watermark_untouched(self):
        try:
            from fpdf import FPDF
        except ImportError:
            self.skipTest("没装 fpdf2")
        pdf = FPDF(unit="pt", format="A4")
        pdf.add_page()
        pdf.set_font("Helvetica", size=10)
        body = ["A", "B list item", "A", "C", "A second section", "Normal line of text"]      # 有重复的单字行,但间距不均匀
        for y, line in zip((100, 130, 200, 215, 400, 430), body):
            pdf.text(60, y, line)
        p = os.path.join(tempfile.mkdtemp(), "plain.pdf")
        pdf.output(p)
        self.assertEqual(extract.extract_pdf(p).split("\n"), body)

    def test_sandbox_subprocess_protocol(self):
        d = tempfile.mkdtemp()
        make_samples.write_docx(os.path.join(d, "a.docx"), "张三 个人简历\n清华大学")
        with zipfile.ZipFile(os.path.join(d, "bad.docx"), "w") as z:
            z.writestr("x.txt", "不是 Word 文档")
        with mock.patch.dict(os.environ, {"RESUME_SCREENER_SANDBOX": "1"}):
            self.assertTrue(extract.use_sandbox())
            self.assertIn("清华大学", extract.extract(os.path.join(d, "a.docx")))
            with self.assertRaises(RuntimeError):
                extract.extract(os.path.join(d, "bad.docx"))
        with mock.patch.dict(os.environ, {"RESUME_SCREENER_SANDBOX": "0"}):
            self.assertFalse(extract.use_sandbox())


class RenameTest(unittest.TestCase):
    def test_guess_name(self):
        g = rename.guess_name_from_text
        self.assertEqual(g("姓名:王小明\n性别:男"), "王小明")
        self.assertEqual(g("姓 名 李华  电话"), "李华")
        self.assertEqual(g("张三 个人简历\n电话"), "张三")
        self.assertEqual(g("求职意向\n赵六 | 男 | 30岁"), "赵六")
        self.assertEqual(g("Name: Wendy Zhang\nEmail"), "Wendy Zhang")
        self.assertEqual(g("个人简历\n教育经历"), "")
        self.assertEqual(g("负责招聘本科及以上学历的销售人员"), "")

    def test_filenames(self):
        from datetime import datetime
        now = datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(rename.new_filename("张三", now=now), "张三-简历-20260102-030405")
        self.assertEqual(rename.new_filename("", now=now), "未知-简历-20260102-030405")
        self.assertEqual(rename.new_filename('a/b:c*"d', "{name}_{ts}", "%Y", now), "abcd_2026")


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.ws, self.made = make_workspace(blacklist=["虚构重工"])
        quiet(make_samples.main, self.ws.inbox)
        Path(self.ws.inbox, "空白.txt").write_text("   \n", encoding="utf-8")
        Path(self.ws.inbox, "陌生人.txt").write_text("一段假模型不认识的简历文字,教育经历\n2010.09-2014.06 示例大学 本科\n\n工作经历\n无", encoding="utf-8")
        Path(self.ws.inbox, "忽略我.png").write_bytes(b"\x89PNG")
        self.model = FakeModel()
        patcher = mock.patch("resume_screener.parse.time.sleep", lambda s: None)      # 重试之间不真的等
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_init_created_everything(self):
        self.assertEqual(self.made, ["rules.json", "院校清单.csv", "校名别名.json"])
        for d in (self.ws.inbox, self.ws.passed, self.ws.rejected, self.ws.failed):
            self.assertTrue(d.is_dir())
        self.assertEqual(init_workspace(self.ws.root)[1], [])           # 再跑一次不覆盖已有文件

    def test_full_run(self):
        results = quiet(pipeline.run, self.ws, chat=self.model)
        by = {r["file"]: r for r in results}
        self.assertEqual(len(results), 6)                                # png 不算
        self.assertEqual(by["示例简历-张三.docx"]["result"], "passed")      # 提到过黑名单公司,但没在那儿任职
        self.assertEqual(by["示例简历-李四.docx"]["result"], "rejected")
        self.assertIn("示例学院", by["示例简历-李四.docx"]["reason"])
        self.assertEqual(by["示例简历-王五.docx"]["result"], "rejected")
        self.assertIn("函授", by["示例简历-王五.docx"]["reason"])
        self.assertEqual(by["示例简历-赵六.docx"]["result"], "rejected")
        self.assertIn("虚构重工", by["示例简历-赵六.docx"]["reason"])
        self.assertEqual(by["空白.txt"]["result"], "failed")
        self.assertEqual(by["陌生人.txt"]["result"], "failed")
        self.assertIn("模型解析失败", by["陌生人.txt"]["reason"])
        # 快筛淘汰的王五和空白文件没有送模型;陌生人重试了 3 次
        self.assertEqual(sorted(set(self.model.seen)), ["?", "张三", "李四", "赵六"])
        self.assertEqual(self.model.seen.count("?"), 3)
        self.assertNotIn("王五", self.model.seen)
        # 文件去向
        self.assertEqual(sorted(p.name for p in self.ws.inbox.iterdir()), ["忽略我.png"])
        passed_dirs = list(self.ws.passed.iterdir())
        self.assertEqual(len(passed_dirs), 1)
        self.assertTrue(passed_dirs[0].name.startswith("张三-"))
        resume = list(passed_dirs[0].iterdir())
        self.assertEqual(len(resume), 1)
        self.assertRegex(resume[0].name, r"^张三-简历-\d{8}-\d{6}\.docx$")
        self.assertEqual(sorted(p.name.split("-")[0] for p in self.ws.rejected.iterdir()), ["李四", "王五", "赵六"])
        self.assertEqual(sorted(p.name for p in self.ws.failed.iterdir()), ["空白.txt", "陌生人.txt"])
        # 汇总表只有通过的人
        got = summary.load_candidates(self.ws.summary_xlsx)
        self.assertEqual([(c["name"], c["city"], c["exp_years"], c["school"]) for c in got], [("张三", "长沙", 9, "清华大学")])
        self.assertEqual(got[0]["resume_path"], str(resume[0].absolute()))
        self.assertIn("处理完成:通过 1,淘汰 3,失败 2", self.ws.log_file.read_text(encoding="utf-8"))
        self.assertEqual(quiet(pipeline.run, self.ws, chat=self.model), [])      # 再跑一遍:没有新简历

    def test_without_blacklist_and_school_rule(self):
        root = self.ws.root
        rules = json.loads((root / "rules.json").read_text(encoding="utf-8"))
        rules["company_blacklist"] = []
        rules["schools"]["require_in_list"] = False
        (root / "rules.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        results = quiet(pipeline.run, load_workspace(root), chat=self.model)
        self.assertEqual(sorted(r["name"] for r in results if r["result"] == "passed"), ["张三", "李四", "赵六"])

    def test_same_name_twice_does_not_overwrite(self):
        shutil.copy(str(self.ws.inbox / "示例简历-李四.docx"), str(self.ws.inbox / "示例简历-李四-副本.docx"))
        quiet(pipeline.run, self.ws, chat=self.model)
        self.assertEqual(len([p for p in self.ws.rejected.iterdir() if p.name.startswith("李四")]), 2)

    def test_missing_school_list_stops_before_touching_files(self):
        (self.ws.root / "院校清单.csv").unlink()
        ws = load_workspace(self.ws.root)
        with self.assertRaises(ConfigError):
            quiet(pipeline.run, ws, chat=self.model)
        self.assertEqual(len(list(ws.inbox.iterdir())), 7)
        self.assertEqual(self.model.seen, [])

    def test_bad_key_stops_the_batch(self):
        calls = []

        def rejected(messages, system=None, max_tokens=None, timeout=None):
            calls.append(1)
            raise llm.LLMConfigError("模型接口返回 401")

        with self.assertRaises(llm.LLMConfigError):
            quiet(pipeline.run, self.ws, chat=rejected)
        self.assertEqual(len(calls), 1)                                   # 不重试,也不继续处理后面的
        self.assertEqual(list(self.ws.failed.iterdir()), [])
        self.assertIn("示例简历-张三.docx", [p.name for p in self.ws.inbox.iterdir()])      # 简历留在原地

    def test_groups_filter(self):
        rules = json.loads((self.ws.root / "rules.json").read_text(encoding="utf-8"))
        rules["schools"]["groups"] = ["海外及港澳台"]
        (self.ws.root / "rules.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        ws = load_workspace(self.ws.root)
        self.assertNotIn("清华大学", ws.pool)
        self.assertIn("剑桥大学", ws.pool)
        rules["schools"]["groups"] = ["没有这个分组"]
        (self.ws.root / "rules.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_workspace(self.ws.root).pool


class CliTest(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_end_to_end(self):
        root = os.path.join(tempfile.mkdtemp(), "ws")
        code, out, _ = self.run_cli("init", root)
        self.assertEqual(code, 0)
        self.assertIn("rules.json", out)
        quiet(make_samples.main, os.path.join(root, "简历", "待处理"))
        model = FakeModel()
        with mock.patch.object(llm, "chat", model), mock.patch.dict(os.environ, {"LLM_API_KEY": "k-test"}):
            code, out, _ = self.run_cli("-w", root, "process", "--json")
        self.assertEqual(code, 0)
        self.assertIn('"result": "passed"', out)
        jd_reply = json.dumps({"thinking": "t", "hard": {"degree_min": "本科", "experience_min": 5, "locations": ["长沙"],
                               "gender": "男", "age_min": None, "age_max": None}, "soft_keywords": ["Python", "微服务"]}, ensure_ascii=False)
        with mock.patch.object(llm, "chat", lambda *a, **k: jd_reply):
            code, out, _ = self.run_cli("-w", root, "match", "招高级后端,长沙,本科,5 年以上,限男性", "-n", "3")
            self.assertEqual(code, 0)
            self.assertIn("1. 张三", out)
            self.assertIn("没有用来筛选的条件:性别", out)
            code, out, _ = self.run_cli("-w", root, "match", "-f", os.path.join(ROOT, "examples", "jd.example.txt"), "--json")
            self.assertEqual(json.loads(out)["candidates"][0]["name"], "张三")
        self.assertEqual(self.run_cli("-w", root, "check-school", "北大")[0], 0)
        self.assertEqual(self.run_cli("-w", root, "check-school", "示例学院")[0], 1)

    def test_interview_and_extract(self):
        from tests.test_llm_match import QUESTIONS
        d = tempfile.mkdtemp()
        resume = os.path.join(d, "r.txt")
        Path(resume).write_text("张三 清华大学 6 年后端", encoding="utf-8")
        with mock.patch.object(llm, "chat", lambda *a, **k: json.dumps(QUESTIONS, ensure_ascii=False)):
            code, out, _ = self.run_cli("-w", d, "interview", "高级后端工程师", resume)      # 没有 rules.json 也能用
            self.assertEqual(code, 0)
            self.assertIn("## 技术题", out)
            target = os.path.join(d, "q.md")
            self.assertEqual(self.run_cli("-w", d, "interview", "岗位", "直接给一段简历文字", "-o", target)[0], 0)
            self.assertIn("### Q1", Path(target).read_text(encoding="utf-8"))
        code, out, _ = self.run_cli("extract", resume)
        self.assertEqual((code, out.strip()), (0, "张三 清华大学 6 年后端"))

    def test_clean_errors(self):
        d = tempfile.mkdtemp()
        self.assertEqual(self.run_cli()[0], 0)
        code, _, err = self.run_cli("-w", d, "process")
        self.assertEqual(code, 2)
        self.assertIn("init", err)
        Path(d, "rules.json").write_text("{oops", encoding="utf-8")
        self.assertEqual(self.run_cli("-w", d, "process")[0], 2)
        root = os.path.join(d, "ws")
        self.run_cli("init", root)
        inbox = os.path.join(root, "简历", "待处理")
        quiet(make_samples.main, inbox)
        with mock.patch.dict(os.environ, {}, clear=True):          # 没设密钥:给出明确提示,简历原地不动
            code, _, err = self.run_cli("-w", root, "match", "招人")
            self.assertEqual(code, 2)
            self.assertIn("LLM_API_KEY", err)
            code, _, err = self.run_cli("-w", root, "process")
            self.assertEqual(code, 2)
            self.assertIn("LLM_API_KEY", err)
        self.assertEqual(len(os.listdir(inbox)), 4)
        self.assertEqual(self.run_cli("extract", os.path.join(d, "没有这个文件.pdf"))[0], 2)


if __name__ == "__main__":
    unittest.main()
