"""模型调用、简历解析重试、JD 反选、面试题的测试。模型一律用假的替身,不联网、不需要密钥。"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from resume_screener import interview, llm, match_jd, summary     # noqa: E402
from resume_screener.parse import parse_resume                    # noqa: E402

GOOD = {"name": "张三", "highest_degree": "本科", "highest_school": "清华大学", "keywords": ["Python"]}


class FakeChat:
    """按顺序吐出预设回复的假模型;回复是异常就抛出。记录每次收到的参数。"""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, messages, system=None, max_tokens=None, timeout=None):
        self.calls.append({"messages": messages, "system": system})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)


class LLMClientTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"LLM_API_KEY": "k-test"}, clear=False)
        self.env.start()
        for k in ("LLM_API_STYLE", "LLM_BASE_URL", "LLM_MODEL", "LLM_MAX_TOKENS", "LLM_TIMEOUT"):
            os.environ.pop(k, None)
        self.sent = []

    def tearDown(self):
        self.env.stop()
        llm.configure(None)

    def post(self, reply):
        def _post(url, headers, payload, timeout):
            self.sent.append((url, headers, payload, timeout))
            return reply
        return mock.patch.object(llm, "_post", _post)

    def test_anthropic_style(self):
        llm.configure({"api_style": "anthropic", "base_url": "https://example.test/anthropic/", "model": "m1", "max_tokens": 5000})
        reply = {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "你好"}]}
        with self.post(reply):
            self.assertEqual(llm.chat([{"role": "user", "content": "hi"}], system="S"), "你好")      # 思考块被跳过
        url, headers, payload, _ = self.sent[0]
        self.assertEqual(url, "https://example.test/anthropic/v1/messages")
        self.assertEqual(headers["x-api-key"], "k-test")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(payload, {"model": "m1", "max_tokens": 5000, "system": "S", "messages": [{"role": "user", "content": "hi"}]})

    def test_openai_style(self):
        llm.configure({"api_style": "openai", "base_url": "https://example.test/v1", "model": "m2", "extra_body": {"temperature": 0}})
        with self.post({"choices": [{"message": {"content": "好的"}, "finish_reason": "stop"}]}):
            self.assertEqual(llm.chat([{"role": "user", "content": "hi"}], system="S", max_tokens=100), "好的")
        url, headers, payload, _ = self.sent[0]
        self.assertEqual(url, "https://example.test/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer k-test")
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "S"})
        self.assertEqual((payload["max_tokens"], payload["temperature"]), (100, 0))

    def test_truncated_refusal_empty(self):
        llm.configure({"api_style": "anthropic"})
        with self.post({"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{\"a\":"}]}):
            with self.assertRaises(llm.LLMTruncated):
                llm.chat([{"role": "user", "content": "x"}])
        with self.post({"stop_reason": "refusal", "content": []}):
            with self.assertRaises(llm.LLMError):
                llm.chat([{"role": "user", "content": "x"}])
        with self.post({"stop_reason": "end_turn", "content": []}):
            with self.assertRaises(llm.LLMError):
                llm.chat([{"role": "user", "content": "x"}])
        llm.configure({"api_style": "openai", "base_url": "https://example.test/v1"})
        with self.post({"choices": [{"message": {"content": "半截"}, "finish_reason": "length"}]}):
            with self.assertRaises(llm.LLMTruncated):
                llm.chat([{"role": "user", "content": "x"}])

    def test_config_sources_and_missing_key(self):
        with mock.patch.dict(os.environ, {"LLM_MODEL": "from-env", "LLM_API_STYLE": "openai", "LLM_MAX_TOKENS": "777"}):
            c = llm.configure({"model": "from-file", "base_url": "https://x.test/v1"})
            self.assertEqual((c["model"], c["api_style"], c["max_tokens"], c["base_url"]), ("from-env", "openai", 777, "https://x.test/v1"))
        with self.assertRaises(llm.LLMError):
            llm.configure({"api_style": "别的"})
        llm.configure({"api_key_env": "MY_KEY"})
        with mock.patch.dict(os.environ, {"MY_KEY": "abc"}):
            self.assertEqual(llm.api_key(), "abc")
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(llm.LLMConfigError):
                llm.api_key()
        self.assertEqual(llm.DEFAULTS["api_key_env"], "LLM_API_KEY")

    def test_extract_json(self):
        self.assertEqual(llm.extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm.extract_json('好的,结果如下:{"a": {"b": [1, 2]}} 请查收'), {"a": {"b": [1, 2]}})
        with self.assertRaises(json.JSONDecodeError):
            llm.extract_json("完全不是 JSON")
        try:
            import json_repair  # noqa: F401
        except ImportError:
            self.skipTest("没装 json_repair,跳过修复用例")
        self.assertEqual(llm.extract_json('{"a": 1, "b": "x",}'), {"a": 1, "b": "x"})


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append((self.path, dict(self.headers), body))
        key = self.headers.get("x-api-key") or self.headers.get("Authorization", "").replace("Bearer ", "")
        if key == "bad":
            code, out = 401, {"error": "invalid key"}
        elif body["model"] == "boom":
            code, out = 500, {"error": "server error"}
        elif self.path.endswith("/v1/messages"):
            code, out = 200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "收到:" + body["messages"][-1]["content"]}]}
        else:
            code, out = 200, {"choices": [{"message": {"content": "收到:" + body["messages"][-1]["content"]}, "finish_reason": "stop"}]}
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class RealHttpTest(unittest.TestCase):
    """走真实的 HTTP 请求路径,对面是本机起的假模型服务。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        llm.configure(None)

    def setUp(self):
        _Handler.seen = []

    def ask(self, cfg, key="good"):
        with mock.patch.dict(os.environ, {"LLM_API_KEY": key}, clear=True):
            llm.configure(cfg)
            return llm.chat([{"role": "user", "content": "你好"}], system="S")

    def test_both_styles_over_http(self):
        self.assertEqual(self.ask({"api_style": "anthropic", "base_url": self.base, "model": "m"}), "收到:你好")
        self.assertEqual(_Handler.seen[0][0], "/v1/messages")
        self.assertEqual(_Handler.seen[0][2]["system"], "S")
        self.assertEqual(self.ask({"api_style": "anthropic", "base_url": self.base + "/v1", "model": "m"}), "收到:你好")
        self.assertEqual(self.ask({"api_style": "openai", "base_url": self.base + "/v1", "model": "m"}), "收到:你好")
        self.assertEqual(_Handler.seen[2][0], "/v1/chat/completions")

    def test_errors_over_http(self):
        with self.assertRaises(llm.LLMConfigError):
            self.ask({"api_style": "anthropic", "base_url": self.base, "model": "m"}, key="bad")
        with self.assertRaises(llm.LLMError) as c:
            self.ask({"api_style": "openai", "base_url": self.base + "/v1", "model": "boom"})
        self.assertNotIsInstance(c.exception, llm.LLMConfigError)
        self.assertIn("500", str(c.exception))
        with self.assertRaises(llm.LLMError):
            self.ask({"api_style": "anthropic", "base_url": "http://127.0.0.1:9", "model": "m", "timeout": 2})


class ParseResumeTest(unittest.TestCase):
    def test_first_try(self):
        chat = FakeChat(GOOD)
        self.assertEqual(parse_resume("张三 清华大学", chat=chat), GOOD)
        self.assertIn("张三 清华大学", chat.calls[0]["messages"][0]["content"])
        self.assertIn("只输出纯 JSON", chat.calls[0]["system"])

    def test_retry_feeds_error_back(self):
        chat = FakeChat("这不是 JSON <<<", GOOD)
        self.assertEqual(parse_resume("张三 清华大学", chat=chat, sleep=lambda s: None), GOOD)
        second = chat.calls[1]["messages"][0]["content"]
        self.assertIn("不是合法 JSON", second)
        self.assertIn("这不是 JSON <<<", second)            # 上一轮的坏输出
        self.assertIn("张三 清华大学", second)               # 原简历
        self.assertIn("highest_school", second)             # 字段要求

    def test_network_error_then_ok(self):
        chat = FakeChat(llm.LLMError("超时"), GOOD)
        self.assertEqual(parse_resume("x", chat=chat, sleep=lambda s: None), GOOD)
        self.assertNotIn("不是合法 JSON", chat.calls[1]["messages"][0]["content"])      # 没拿到回复就按原样重发

    def test_gives_up_after_three(self):
        chat = FakeChat("bad1", "bad2", "bad3")
        with self.assertRaises(RuntimeError):
            parse_resume("x", chat=chat, sleep=lambda s: None)
        self.assertEqual(len(chat.calls), 3)

    def test_truncated_does_not_retry(self):
        chat = FakeChat(llm.LLMTruncated("截断"), GOOD)
        with self.assertRaises(llm.LLMTruncated):
            parse_resume("x", chat=chat, sleep=lambda s: None)
        self.assertEqual(len(chat.calls), 1)

    def test_config_error_does_not_retry(self):
        chat = FakeChat(llm.LLMConfigError("401"), GOOD)
        with self.assertRaises(llm.LLMConfigError):
            parse_resume("x", chat=chat, sleep=lambda s: None)
        self.assertEqual(len(chat.calls), 1)

    def test_long_resume_is_cut(self):
        chat = FakeChat(GOOD)
        parse_resume("甲" * 50000, chat=chat)
        self.assertLess(len(chat.calls[0]["messages"][0]["content"]), 14000)


def cand(**kw):
    base = {"name": "张三", "gender": "男", "age": 30, "degree": "本科", "school": "清华大学", "major": "计算机",
            "city": "长沙", "exp_years": 6, "keywords": ["Python", "Go", "微服务", "Kubernetes"],
            "summary": "负责订单系统的微服务拆分,维护 Kubernetes 集群", "resume_path": ""}
    base.update(kw)
    return base


JD_REPLY = {"thinking": "本科、5 年、长沙是硬性;技能是软性", "hard": {"degree_min": "本科", "experience_min": 5, "locations": ["长沙"],
            "gender": "男", "age_min": None, "age_max": 35}, "soft_keywords": ["Python", "微服务", "Kubernetes", "支付"]}


class MatchTest(unittest.TestCase):
    def test_extract_requirements(self):
        req = match_jd.extract_jd_requirements("JD", chat=FakeChat("```json\n%s\n```" % json.dumps(JD_REPLY, ensure_ascii=False)))
        self.assertEqual(req["hard"]["degree_min"], "本科")
        self.assertEqual(req["hard"]["locations"], ["长沙"])
        self.assertEqual(req["soft_keywords"], JD_REPLY["soft_keywords"])
        fallback = match_jd.extract_jd_requirements("JD", chat=FakeChat("Python、微服务\n支付"))
        self.assertEqual(fallback["hard"], match_jd.default_hard())            # 解析不出 JSON → 不设硬性条件
        self.assertEqual(fallback["soft_keywords"], ["Python", "微服务", "支付"])

    def test_hard_filter_degree_experience_location(self):
        hard = dict(match_jd.default_hard(), degree_min="硕士", experience_min=5, locations=["湖南"])
        f = lambda **kw: match_jd.hard_filter(cand(**kw), hard)
        self.assertEqual(f(degree="硕士研究生")[0], True)                        # 省 → 市也对得上
        self.assertEqual(f(degree="本科")[2], "degree")
        self.assertEqual(f(degree="")[2], "degree")
        self.assertEqual(f(degree="博士", exp_years=3)[2], "experience")
        self.assertEqual(f(degree="博士", exp_years="")[0], True)                # 年限未知 → 放行
        self.assertEqual(f(degree="博士", city="上海")[2], "location")
        self.assertEqual(f(degree="博士", city="")[0], True)                     # 所在地未知 → 放行
        self.assertTrue(match_jd.hard_filter(cand(degree="大专"), match_jd.default_hard())[0])

    def test_gender_and_age_off_by_default(self):
        hard = dict(match_jd.default_hard(), gender="男", age_max=35)
        woman, older = cand(gender="女"), cand(age=40)
        self.assertTrue(match_jd.hard_filter(woman, hard)[0])                  # 默认不按性别 / 年龄筛
        self.assertTrue(match_jd.hard_filter(older, hard)[0])
        self.assertEqual(match_jd.ignored_conditions(hard), ["性别", "年龄"])
        self.assertEqual(match_jd.hard_filter(woman, hard, allow_gender=True)[2], "gender")
        self.assertEqual(match_jd.hard_filter(older, hard, allow_age=True)[2], "age")
        self.assertTrue(match_jd.hard_filter(cand(gender="", age=""), hard, True, True)[0])      # 未知 → 放行
        self.assertEqual(match_jd.ignored_conditions(hard, True, True), [])

    def test_soft_score_formula(self):
        score, hit = match_jd.soft_score(["Python", "微服务", "Kubernetes", "支付"], cand())
        # 覆盖 3/4;Jaccard 3/5;总结里命中 微服务、kubernetes 2/4
        self.assertAlmostEqual(score, 0.75 * 0.5 + 0.6 * 0.3 + 0.5 * 0.2)
        self.assertEqual(hit, ["kubernetes", "python", "微服务"])
        self.assertEqual(match_jd.soft_score([], cand()), (0.0, []))
        self.assertEqual(match_jd.soft_score(["Java"], cand(keywords=[], summary=""))[0], 0.0)
        self.assertGreater(match_jd.soft_score(["python"], cand())[0], 0)       # 不分大小写

    def test_match_ranks_and_reports(self):
        people = [cand(name="甲"), cand(name="乙", keywords=["Java"], summary="负责支付网关"),
                  cand(name="丙", degree="大专"), cand(name="丁", city="上海"), cand(name="戊", gender="女", age=50)]
        res = match_jd.match("JD", people, top=2, include_failed=True, chat=FakeChat(JD_REPLY))
        self.assertEqual([c["name"] for c in res["candidates"]], ["甲", "戊"])   # 性别 / 年龄默认不筛,戊与甲同分
        self.assertEqual((res["total"], res["passed_count"], res["failed_count"]), (5, 3, 2))
        self.assertEqual({c["name"]: c["fail_field"] for c in res["failed"]}, {"丙": "degree", "丁": "location"})
        self.assertEqual(res["ignored"], ["性别", "年龄"])
        json.dumps(res, ensure_ascii=False)                                     # 结果可以直接转 JSON
        strict = match_jd.match("JD", people, allow_gender=True, allow_age=True, chat=FakeChat(JD_REPLY))
        self.assertEqual([c["name"] for c in strict["candidates"]], ["甲", "乙"])
        self.assertEqual(strict["failed"], [])

    def test_summary_roundtrip(self):
        xlsx = os.path.join(tempfile.mkdtemp(), "out", "汇总表.xlsx")
        resume = os.path.join(os.path.dirname(xlsx), "a.pdf")
        info = {"name": "张三", "gender": None, "age": None, "phone": "000-0000-0001", "email": "zhangsan@example.com",
                "current_location": "长沙", "highest_degree": "本科", "highest_school": "清华大学", "highest_major": "计算机",
                "total_work_years": 6, "keywords": ["Python", "Go"], "last_two_jobs_summary": "做后端"}
        self.assertEqual(summary.append_candidate(xlsx, info, resume), 1)
        self.assertEqual(summary.append_candidate(xlsx, dict(info, name="李四", total_work_years=None), resume), 2)
        got = summary.load_candidates(xlsx)
        self.assertEqual([c["name"] for c in got], ["张三", "李四"])
        self.assertEqual((got[0]["city"], got[0]["exp_years"], got[0]["keywords"], got[0]["degree"]), ("长沙", 6, ["Python", "Go"], "本科"))
        self.assertEqual(got[1]["exp_years"], None)
        self.assertTrue(got[0]["resume_path"].endswith("a.pdf"))
        self.assertEqual(summary.load_candidates(os.path.join(tempfile.mkdtemp(), "none.xlsx")), [])
        # 表里的列被人调了顺序,追加也不会错位
        from openpyxl import load_workbook
        wb = load_workbook(xlsx)
        ws = wb[summary.SHEET]
        a, b = ws.cell(1, 2).value, ws.cell(1, 9).value
        ws.cell(1, 2).value, ws.cell(1, 9).value = b, a
        for r in (2, 3):
            x, y = ws.cell(r, 2).value, ws.cell(r, 9).value
            ws.cell(r, 2).value, ws.cell(r, 9).value = y, x
        wb.save(xlsx)
        summary.append_candidate(xlsx, dict(info, name="王五"), resume)
        self.assertEqual([c["name"] for c in summary.load_candidates(xlsx)], ["张三", "李四", "王五"])
        self.assertEqual([c["school"] for c in summary.load_candidates(xlsx)], ["清华大学"] * 3)


QUESTIONS = {"thinking": "重点看微服务经验", "candidate_summary": {"name": "张三", "years_of_experience": 6, "level": "高级",
             "path": "社招", "background_summary": "后端工程师"},
             "questions": [
                 {"category": "技术", "title": "讲讲订单系统的拆分过程", "difficulty": "高级", "hints": ["边界", "数据一致性"],
                  "key_points": ["按业务边界拆", "先拆读后拆写"], "evaluation_dimensions": ["架构能力", "表达"],
                  "follow_up_if_good": "如果重来会怎么做", "follow_up_if_bad": "什么是分布式事务"},
                 {"category": "行为", "title": "讲一次线上事故", "difficulty": "中级", "evaluation_dimensions": ["复盘", "担当"]},
                 {"category": "反问", "title": "你想了解团队的什么", "difficulty": "初级"}]}


class InterviewTest(unittest.TestCase):
    def test_generate_and_render(self):
        chat = FakeChat(QUESTIONS)
        r = interview.generate_interview("高级后端工程师", "张三 清华大学 6 年后端", n_tech=1, n_behavior=1, n_reverse=1, chat=chat)
        self.assertTrue(r["ok"])
        self.assertEqual(r["attempts"], 1)
        self.assertIn("高级后端工程师", chat.calls[0]["messages"][0]["content"])
        self.assertIn("资深面试官", chat.calls[0]["system"])
        md = interview.render_markdown(r["data"], "高级后端工程师")
        for part in ("# 面试题:高级后端工程师 · 张三", "## 技术题(1 道)", "### Q1(高级)", "讲讲订单系统的拆分过程",
                     "答得好,追问:如果重来会怎么做", "## 行为题(1 道)", "### Q3(初级)"):
            self.assertIn(part, md)

    def test_retry_and_failures(self):
        chat = FakeChat("不是 JSON", QUESTIONS)
        r = interview.generate_interview("岗位", "简历", chat=chat)
        self.assertTrue(r["ok"])
        self.assertEqual(r["attempts"], 2)
        self.assertEqual([m["role"] for m in chat.calls[1]["messages"]], ["user", "assistant", "user"])
        self.assertFalse(interview.generate_interview("", "简历", chat=FakeChat())["ok"])
        self.assertFalse(interview.generate_interview("岗位", " ", chat=FakeChat())["ok"])
        bad = interview.generate_interview("岗位", "简历", max_retries=1, chat=FakeChat({"x": 1}, {"questions": []}))
        self.assertEqual((bad["ok"], bad["attempts"]), (False, 2))

    def test_pdf(self):
        try:
            import fpdf  # noqa: F401
            font = interview.find_chinese_font()
        except (ImportError, FileNotFoundError):
            self.skipTest("没装 fpdf2 或没有中文字体,跳过 PDF 用例")
        data = interview.generate_interview_pdf(QUESTIONS, "高级后端工程师", font)
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertGreater(len(data), 3000)
        with self.assertRaises(ValueError):
            interview.generate_interview_pdf({"questions": []})


if __name__ == "__main__":
    unittest.main()
