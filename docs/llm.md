# 模型配置

程序支持两种接口格式，覆盖了市面上绝大多数模型服务：

| `api_style` | 请求发到 | 适用 |
|---|---|---|
| `anthropic` | `{base_url}/v1/messages` | Anthropic 官方接口，以及提供 Anthropic 兼容接口的服务 |
| `openai` | `{base_url}/chat/completions` | OpenAI 官方接口，以及提供 OpenAI 兼容接口的服务 |

## 在哪里配置

两个地方，环境变量优先：

| `rules.json` 的 `llm` 段 | 环境变量 | 默认值 |
|---|---|---|
| `api_style` | `LLM_API_STYLE` | `anthropic` |
| `base_url` | `LLM_BASE_URL` | `https://api.anthropic.com` |
| `model` | `LLM_MODEL` | `claude-opus-5-5` |
| `max_tokens` | `LLM_MAX_TOKENS` | `16000` |
| `timeout`（秒） | `LLM_TIMEOUT` | `120` |
| `api_key_env` | — | `LLM_API_KEY` |
| `extra_body` | — | `{}` |

**密钥只从环境变量读。** 默认读 `LLM_API_KEY`；想用别的变量名，把 `api_key_env` 改成那个名字。没设 `LLM_API_KEY` 时，`anthropic` 格式会再试 `ANTHROPIC_API_KEY`，`openai` 格式会再试 `OPENAI_API_KEY`。

## 三种常见配法

**Anthropic 官方接口（默认）**

```bash
export LLM_API_KEY=你的密钥
```

`rules.json` 不用改。

**Anthropic 兼容接口**

很多模型服务提供 Anthropic 兼容的端点（这个项目最初就是接在这样的端点上运行的）。把 `base_url` 和 `model` 换成对方给的：

```json
"llm": {
  "api_style": "anthropic",
  "base_url": "https://服务商给的地址/anthropic",
  "model": "服务商的模型名",
  "max_tokens": 4096
}
```

**OpenAI 兼容接口**

`base_url` 要包含到 `/v1` 为止：

```json
"llm": {
  "api_style": "openai",
  "base_url": "https://服务商给的地址/v1",
  "model": "服务商的模型名",
  "max_tokens": 4096
}
```

## 几个注意点

**`max_tokens` 默认给得比较大。** 新一代的模型默认会先思考再回答，思考也占 `max_tokens`，给小了正文会被截断。但有些服务的上限比较低，超了会报错——遇到这种情况把它调小（一份简历的解析结果通常 2000 以内）。

**输出被截断时程序会直接报错**，并提示调大 `max_tokens`，不会拿半截 JSON 去重试。

**`extra_body`** 里的内容会原样合并进请求体，用来加服务商特有的参数。比如有的 OpenAI 兼容服务支持强制 JSON 输出：

```json
"extra_body": {"response_format": {"type": "json_object"}}
```

**选什么模型。** 简历解析是信息抽取，对模型要求不高，但准确性直接影响筛选结果（比如把「在职研究生」判成全日制）。建议先拿十几份你熟悉的简历试跑，对照日志里的淘汰原因看有没有判错，再决定用哪个模型。

**超时和重试。** 单次请求默认 120 秒超时。简历解析遇到「回复不是合法 JSON」或网络错误会重试，最多 3 次，间隔逐次加长。

## 数据去向

发送到你配置的 `base_url` 的内容只有这些：甄选时是简历文本（前 12000 字），出面试题时是岗位名称和简历文本（前 6000 字），JD 反选时是岗位要求本身（候选人信息不会发出去，过滤和打分都在本地做）。除此之外程序不向任何地方发送数据。用哪家服务、数据会不会被留存，取决于你和服务商的约定。
