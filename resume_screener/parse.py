"""简历结构化解析:简历文本 → 字段齐全的 dict。"""
import json
import time

from . import llm

MAX_RESUME_CHARS = 12000

SYSTEM_PROMPT = """你是 HR 简历解析助手,从简历文本里抽取结构化信息。
严格遵守:
- 只输出纯 JSON,不要任何额外文字,不要 markdown 代码块,不要注释
- 字符串里的换行用 \\n 转义,引号用 \\" 转义
- 缺失的字段填 null,不要猜,不要填空字符串
- 只用简历里写了的信息,不许编造
"""

USER_TEMPLATE = """请解析下面这份简历:

```
{resume_text}
```

输出 JSON,字段如下:
{{
  "name": str|null,
  "gender": "男"|"女"|null,
  "age": int|null,
  "phone": str|null,
  "email": str|null,
  "current_location": str|null,
  "highest_degree": "博士"|"硕士"|"本科"|"专科"|null,
  "highest_school": str|null,
  "highest_major": str|null,
  "is_full_time": bool|null,
  "is_zhengshou": bool|null,
  "is_zhuan_sheng_ben": bool|null,
  "total_work_years": int|null,
  "education_history": [
    {{"period": str, "school": str, "major": str, "degree": str, "mode": str|null}}
  ],
  "work_history": [
    {{"period": str, "company": str, "title": str, "responsibilities": [str]}}
  ],
  "project_history": [
    {{"period": str, "name": str, "role": str|null, "achievements": str|null}}
  ],
  "last_two_jobs_summary": str,
  "keywords": [str]
}}

抽取要求:
1. gender:只有简历**明确写了**性别才填;不许从姓名推测,没写就 null。
2. age:只有简历写了年龄或出生年月才填;不许用毕业时间倒推。
3. current_location:现居住地或工作地,精确到市;没写就 null。
4. highest_degree:从「博士 / 硕士 / 本科 / 专科」里选最高的一个;highest_school、highest_major 是这段最高学历对应的学校和专业。
5. 学历类型三个字段都按「简历明确写了才判」:
   - is_full_time:明确写了全日制填 true,明确写了非全日制 / 在职 / 函授 / 自考 / 成人教育 / 网络教育填 false,没写填 null
   - is_zhengshou:明确写了统招填 true,明确是非统招填 false,没写填 null
   - is_zhuan_sheng_ben:明确是专升本填 true,否则填 false;看不出来填 null
6. total_work_years:工作总年限,整数(从最早一段工作开始算到现在);简历里完全没有时间信息填 null。
7. period 统一写成 "YYYY.MM-YYYY.MM",月份补足两位;仍在职 / 在读写 "YYYY.MM-至今"。
8. education_history 按时间升序,全部列出;work_history 按时间降序(最近的在前),company 写公司全称。
9. responsibilities:每段任职的职责,拆成独立短句的数组,没有就给 []。
10. last_two_jobs_summary:80 字以内,概括最近两段工作做了什么。
11. keywords:10 个左右关键词(技能 / 行业 / 职位),用简历里的原词,用于之后按岗位要求匹配。
"""


def parse_resume(resume_text, chat=None, timeout=None, retries=3, sleep=None):
    """简历文本 → dict。模型回的不是合法 JSON 时,把错误和上一轮的坏输出喂回去让它重出,最多试 retries 次。"""
    chat = chat or llm.chat
    sleep = sleep or time.sleep
    resume_text = resume_text[:MAX_RESUME_CHARS]
    last_err, last_raw = None, None
    for attempt in range(retries):
        if attempt:
            sleep(1.2 * attempt)      # 退避:限流和网络抖动都友好些
        try:
            if last_raw is None:      # 第一次,或者上一次连回复都没拿到
                prompt = USER_TEMPLATE.format(resume_text=resume_text)
            else:                     # 上一次拿到了回复但不是合法 JSON:告诉模型错在哪
                prompt = ("你上一轮的回复不是合法 JSON(错误:%s)。\n"
                          "请重新抽取,并且只输出严格的 JSON:\n"
                          "- 不要 markdown 代码块\n- 字符串里的换行用 \\n 转义,引号用 \\\" 转义\n"
                          "- 不要尾随逗号\n- 所有括号完整闭合\n\n"
                          "字段要求与上一轮相同:\n%s\n\n"
                          "上一轮的坏输出(仅供参考):\n```\n%s\n```\n\n请重新输出 JSON:"
                          % (last_err, USER_TEMPLATE.format(resume_text=resume_text[:8000]), last_raw[:1500]))
            raw = chat([{"role": "user", "content": prompt}], system=SYSTEM_PROMPT, timeout=timeout)
            last_raw = raw
            return llm.extract_json(raw)
        except (llm.LLMTruncated, llm.LLMConfigError):
            raise                     # 被截断、密钥有问题时,重试没用
        except json.JSONDecodeError as e:
            last_err = e
        except Exception as e:
            last_err, last_raw = e, None
    raise RuntimeError("模型解析 %d 次都失败: %s" % (retries, last_err))
