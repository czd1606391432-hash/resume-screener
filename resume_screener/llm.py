"""大模型调用:只用标准库,支持两种接口格式。

  anthropic  Anthropic Messages API 及其兼容端点(POST {base_url}/v1/messages)
  openai     OpenAI Chat Completions 及其兼容端点(POST {base_url}/chat/completions)

配置来源(后者覆盖前者):rules.json 的 "llm" 段 → 环境变量
  LLM_API_STYLE / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_MAX_TOKENS / LLM_TIMEOUT
密钥只从环境变量读,不写进任何文件。
"""
import json
import os
import re
import urllib.error
import urllib.request

DEFAULTS = {
    "api_style": "anthropic",
    "base_url": "https://api.anthropic.com",
    "model": "claude-opus-5-5",
    "api_key_env": "LLM_API_KEY",
    "max_tokens": 16000,
    "timeout": 120,
    "extra_body": {},
}
_config = dict(DEFAULTS)


class LLMError(RuntimeError):
    pass


class LLMConfigError(LLMError):
    """配置问题:没有密钥,或者密钥被拒绝。重试没有用,整批任务应该立刻停下来。"""


class LLMTruncated(LLMError):
    """输出被 max_tokens 截断。重试没有用,需要调大 max_tokens。"""


def configure(cfg=None):
    """用 rules.json 的 llm 段更新配置;环境变量优先。"""
    global _config
    c = dict(DEFAULTS)
    c.update({k: v for k, v in (cfg or {}).items() if v not in (None, "")})
    env = os.environ
    for key, name in (("api_style", "LLM_API_STYLE"), ("base_url", "LLM_BASE_URL"), ("model", "LLM_MODEL")):
        if env.get(name):
            c[key] = env[name]
    for key, name in (("max_tokens", "LLM_MAX_TOKENS"), ("timeout", "LLM_TIMEOUT")):
        if env.get(name):
            c[key] = int(env[name])
    if c["api_style"] not in ("anthropic", "openai"):
        raise LLMError("api_style 只能是 anthropic 或 openai")
    _config = c
    return c


def api_key():
    env = os.environ
    names = [_config.get("api_key_env") or "LLM_API_KEY", "LLM_API_KEY",
             "ANTHROPIC_API_KEY" if _config["api_style"] == "anthropic" else "OPENAI_API_KEY"]
    for n in names:
        if env.get(n):
            return env[n].strip()
    raise LLMConfigError("没有找到模型密钥:请设置环境变量 %s" % names[0])


def _post(url, headers, payload, timeout):
    """发请求,返回解析后的 JSON。测试里替换这个函数就不会联网。"""
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = "模型接口返回 %s: %s" % (e.code, e.read().decode("utf-8", errors="ignore")[:500])
        raise (LLMConfigError if e.code in (401, 403) else LLMError)(msg)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError("模型接口连不上: %s" % e)


def chat(messages, system=None, max_tokens=None, timeout=None):
    """messages: [{"role": "user" | "assistant", "content": str}, ...];返回模型回复的文字。"""
    c = _config
    base = c["base_url"].rstrip("/")
    limit = int(max_tokens or c["max_tokens"] or 0)
    timeout = timeout or c["timeout"]
    if c["api_style"] == "anthropic":
        payload = {"model": c["model"], "max_tokens": limit or 16000, "messages": messages}
        if system:
            payload["system"] = system
        payload.update(c.get("extra_body") or {})
        url = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        data = _post(url, {"Content-Type": "application/json", "x-api-key": api_key(),
                           "anthropic-version": "2023-06-01"}, payload, timeout)
        if data.get("stop_reason") == "refusal":
            raise LLMError("模型拒绝了这次请求")
        # 回复里可能夹着思考块等其它类型,只取文字块
        text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        if data.get("stop_reason") == "max_tokens":
            raise LLMTruncated("模型输出被截断,请调大 max_tokens(当前 %s)" % payload["max_tokens"])
    else:
        msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
        payload = {"model": c["model"], "messages": msgs}
        if limit:
            payload["max_tokens"] = limit
        payload.update(c.get("extra_body") or {})
        data = _post(base + "/chat/completions",
                     {"Content-Type": "application/json", "Authorization": "Bearer " + api_key()}, payload, timeout)
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        if choice.get("finish_reason") == "length":
            raise LLMTruncated("模型输出被截断,请调大 max_tokens(当前 %s)" % (limit or "未设置"))
    if not text.strip():
        raise LLMError("模型返回了空内容: %s" % str(data)[:300])
    return text


def extract_json(text):
    """从模型回复里取出 JSON 对象。容忍 ```json 代码块、前后多余文字;装了 json_repair 时还能修轻微损坏。"""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        body = m.group(1)
    else:
        s, e = text.find("{"), text.rfind("}")
        body = text[s:e + 1] if s >= 0 and e > s else text
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as err:
        try:
            import json_repair
            obj = json_repair.loads(body)
        except Exception:
            raise err
        if not isinstance(obj, dict) or not obj:
            raise err
    if not isinstance(obj, dict):
        raise json.JSONDecodeError("不是 JSON 对象", body, 0)
    return obj
