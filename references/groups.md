# 分组含义与已知坑

实时数据以 `packyctl groups` 为准（倍率会调）。下面是分组语义和踩过的坑（2026-11 实测）。

## Claude

| 分组 | 倍率 | 说明 |
|---|---|---|
| `cc-sale` | 0.8 | 最便宜的 Claude 分组，官方备注"缓存可能会有异常"。探针缓存正常，真实流量 `cache_ratio` 按预期扣；命中率要用真实流量看。价格表列了 `claude-haiku-4-5-20251001` 但上游 404。 |
| `cc` | 2.5 | **Claude Code 专用，禁止代理 / 二次分发 / API 转接**。上游校验客户端，非官方请求 403/400 并触发反滥用。`auto` 分组默认落这里。**不要探针、不要建议用户拿它做中转。** |
| `cc-expensive` | 2.8 | 官方说"可用于第三方"，第三方中转就用它。坑：`claude-haiku-5-5` 偶发按 ~25k 输入 token 计费（同一提示一次 50 token、一次 25191 token，≈390×）——上游多通道随机路由，某条通道注入大段系统提示并照常计费，响应 usage 和日志一致。`claude-sonnet-4-5-20250929` 曾返回 500。`claude-haiku-5-5` 的 `model_ratio 0.05` 疑似标错，照价扣。 |
| `claude-officially` | 7 | 官方直连，贵 2.5 倍，稳定性最好。 |

## OpenAI / Codex

| 分组 | 倍率 | 说明 |
|---|---|---|
| `codex-sale` | 0.3 | 优惠版。上游固定注入 4.4k~7.3k token 的 system prompt（`gpt-6.2-astra` 7258），首次全价、之后缓存读（0.05~0.1）。短请求成本被放大，算路由分数时要加这笔固定开销。 |
| `codex` | 0.5 | 同上，正价。 |
| `azure-officially` | 5 | Azure 官方。 |
| `grok-sale` / `grok-officially` | 0.3 / 2.5 | `grok-sale` 只接 Responses 协议（`/v1/responses`），chat/completions 不可用。 |

## 国产 / 其它

| 分组 | 说明 |
|---|---|
| `deepseek-sale` / `deepseek-officially` | `deepseek-sale` 有 `model_group_ratio` 覆盖（0.4~0.5，比 `group_ratio` 1 便宜），**高峰加价 ×2**（工作日 9-12 / 14-18 Asia/Shanghai）。`deepseek-v4-flash` 只在 `deepseek-sale`。 |
| `glm-sale` / `zai-officially` | 智谱；`glm-5` 有 `tiers`（32k 以上 ratio 1.5）。 |
| `kimi-sale` / `kimi-officially`、`bailian`、`qwen`、`mimo-officially`、`minimax-officially`、`hunyuan-officially` | 国产模型，`price_currency: CNY` 但扣费口径与 USD 模型一样。 |
| `image` / `image-sale` / `video` | 按次计费（`quota_type: 1`），`model_price` 是每次价格；`image` / `video` 有大范围 `model_group_ratio`。 |
| `gemini-slb` | Gemini 企业版，协议 `gemini`，探针未验证。 |
| `default` | 用户默认分组，key 不填 `group` 时跟随它。 |

## 通用坑

- **分组名 = key 绑定**：一把 key 只属于一个分组（`group_mode: single`）。要用多个分组就每组建一把 key。
- **正价分组叫 `*-officially`**（`deepseek-officially`、`grok-officially`），不是 `*-official`。
- **重复请求体被封**：同一 key × 模型连续发相同内容，上游判"监控探针重复模式"返回 403，临时封禁。测试时每次改内容。
- **多通道随机路由**：同一分组背后多条通道，延迟、注入 token、偶发 500 都可能因通道而异，单次结果不代表分组整体。
