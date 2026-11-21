---
name: packy
description: 读取和管理 PackyAPI（packyapi.com，new-api 分支的模型中转站）账户：查余额、查某模型在各分组的真实价格、找最便宜的分组、看扣费日志和消费汇总、管理 API key（建/停/删/改额度）、发探针核对真实扣费。只要用户提到 PackyAPI / packyapi / packy、分组倍率（cc-sale、cc-expensive、codex-sale …）、"这个模型哪个分组便宜"、"余额还有多少"、"这把 key 花了多少"、"帮我建一把 xx 分组的 key"，即使没说"PackyAPI"三个字，也用这个 skill。
---

# PackyAPI 账户读取与管理

所有操作走 `scripts/packyctl.py`（纯标准库，Python 3.10+），不要自己拼 curl。
加 `--json` 得到结构化输出；写操作默认 dry-run，带 `--apply` 才执行。

```bash
P="python3 <skill目录>/scripts/packyctl.py"   # 安装后也可直接 `packyctl`
```

## 先判断用户要什么

| 用户在问 | 用 | 备注 |
|---|---|---|
| 余额 / 还能用多久 | `balance`，再 `spend --since 24h --by day` 估算日耗 | 金额先报 ¥，括号 ≈$ |
| 某模型多少钱 / 哪个分组便宜 | `price MODEL`，`cheapest MODEL` | `cheapest` 默认只看本地有 key 的分组且排除 `cc` |
| 有哪些分组 / 分组是干嘛的 | `groups` | `override` 列表示该分组对部分模型有单独倍率 |
| 有哪些模型 / 支持什么协议 | `models [-g 分组] [-e anthropic] [-s 关键字]` | |
| 现在是不是高峰加价 | `peak` | 目前只对 deepseek 系 |
| 花了多少 / 哪把 key 在烧钱 | `spend --since 7d --by key|group|model|day` | |
| 某次调用扣了多少 / 扣费对不对 | `logs --since 2h -m MODEL --verify` | `expected` 列 ✗ = 与公式不符 |
| 建 / 停 / 删 key，改额度 | `key create|enable|disable|update|delete` | 先 dry-run 给用户看，确认后 `--apply` |
| 某分组现在能不能用、实扣是不是价格表的价 | `probe MODEL -g GROUP --apply` | 会花钱，先给用户看预估 |
| 留存价格表 | `snapshot` | |

## 硬规则（违反会出错或触发上游风控）

1. **管理接口只在主站** `https://www.packyapi.com/api/*`；**模型调用只走** `https://cf.api.fan/v1/*`。`cf.api.fan/api/*` 返回 404，主站模型调用有区域限制。
2. **请求必须带非默认 User-Agent**。Cloudflare 会拦 `Python-urllib`、`python-requests` 等默认 UA，返回 `403 error code: 1010`。`packyctl` 已处理。
3. **平台所有金额是人民币**。页面和 JSON 里的 "$" 只是符号：`ratio 1 = ¥2/M input token`，`500000 quota = ¥1`，余额、key 额度、日志 `quota` 全是人民币。换算美元用 `CNY_PER_USD`（`.env`，默认 7）。回答用户时先 ¥ 后 ≈$。
4. **`cc` 分组禁止经代理 / 探针调用**。它只放行 Claude Code 官方客户端并明文禁止二次分发，非官方请求 403 并触发反滥用。不要对 `cc` 建探针、不要建议用户用 `cc` 做中转。Claude 走 `cc-sale` / `cc-expensive`。
5. **探针内容必须随机**。重复相同请求体会被上游判为"监控探针重复模式"（403，按 key×模型临时封）。`packyctl probe` 已加随机 nonce。
6. **`catalog_version` 不能当调价信号**，它在多个后端之间来回跳。判断调价要比价格字段本身（用 `snapshot` 留底后 diff）。
7. **余额差不能当某次调用的花费**（账户下其它 key 同时在跑），要用 `logs` 里的 `quota` 求和。
8. **新建 key 的明文只在创建响应里出现一次**，`keys` 列表只有 `key_hint` 后 8 位。创建后立刻把 `PACKY_KEY_<GROUP>=...` 写进 `.env`，不要在对话里复述完整 key。
9. 不输出 `.env` 里任何 key / access token 的明文。

## 读结果时的要点

- `price` 输出的 `g.ratio` 是**该分组对该模型的有效倍率**：`model_group_ratio[group][model]` 优先于 `group_ratio[group]`（flags 带 `*`）。
- 日志 `prompt_tokens` 口径不同：Anthropic 协议（`other.claude=true`）**不含**缓存 token；OpenAI / Responses 协议**含**缓存，要减 `other.cache_tokens`。`packyctl` 的 `in` 列已统一为"不含缓存"。
- 日志 `other.cache_creation_ratio` 是上游按厂商给的默认值（Claude 1.25，GPT/Grok 1.0），价格表里的 `cache_creation_ratio_5m` 不一定一致；以日志为准。
- 日志 `prompt_tokens` 远大于请求实际 token（如 ~25k）= 上游某条通道注入了系统提示并照常计费，不是你的问题；多发生在 `cc-expensive/claude-haiku-5-5`。`probe` 输出 `input_inflated: true` 标记。
- codex 系分组上游固定注入 4k~7k token 的 system prompt，首次全价、之后走缓存读。短请求成本被放大。
- `price_currency` 字段只是展示标签，平台扣费不做汇率换算。
- `tiers` 字段（如 glm-5）= 按输入长度分段的 `model_ratio`，`price` 命令按基础倍率算，超过阈值会更贵。
- `quota_type: 1` 是按次计费（图片 / 视频），`models` 默认隐藏，`--all` 显示。

## 细节文档

- `references/api.md`：五个接口的参数、响应字段、分页上限、鉴权头
- `references/pricing.md`：扣费公式、倍率优先级、高峰加价、单位换算
- `references/groups.md`：各分组含义与已知坑
- `references/runbooks.md`：余额不足 / 分组不可用 / 扣费对不上 / 403 的处置步骤
