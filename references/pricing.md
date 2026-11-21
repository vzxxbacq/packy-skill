# 计费模型

## 单位

| 平台里的数 | 实际含义 |
|---|---|
| `ratio 1` | ¥2 / M input token（new-api 约定 `0.002 / 1k`，页面显示 "$" 但充值 1:1 人民币） |
| `quota 500000` | ¥1 |
| 余额 / key 额度 / 日志 `quota` | 全部人民币 |
| `price_currency: "USD" / "CNY"` | 仅展示标签，扣费不做汇率换算（glm-5 标 ¥4/M、ratio 2，扣的就是 4 个单位/M） |

美元换算是我们自己的事：`USD = CNY / CNY_PER_USD`（`.env`，默认 7）。
回答用户时**先 ¥ 后 ≈$**。

## 倍率优先级

```
mr   = model_ratio                                   # 价格表 data[].model_ratio（有 tiers 时按输入长度分段）
gr   = model_group_ratio[group][model] ?? group_ratio[group]   # 覆盖优先
peak = peak_active[model] ?? 1                       # 高峰加价，目前只对 deepseek 系（工作日 9-12 / 14-18 Asia/Shanghai ×2）
```

每 M token 的平台价（¥）：

| 项 | 公式 |
|---|---|
| input | `mr × gr × peak × 2` |
| output | `input × completion_ratio` |
| cache read | `input × cache_ratio` |
| cache write (5m) | `input × cache_creation_ratio_5m` |

## 单次请求扣费（已用真实日志验证精确成立）

```
weighted = prompt_tokens(不含缓存)
         + completion_tokens × completion_ratio
         + cache_tokens      × cache_ratio
         + cache_creation_tokens × cache_creation_ratio
quota    = mr × gr × peak × weighted        # 0.002/1000 × 500000 = 1，所以 quota 就是 mr×gr×weighted
```

`packyctl logs --verify` 用日志 `other` 里的倍率复算，误差 > max(1, 2%) 标 ✗。

## 已知差异

- **`cache_creation_ratio`**：日志按厂商给默认值（Claude / DeepSeek 1.25，GPT / Grok 1.0），价格表字段不可靠（GPT 写 1.25、DeepSeek 为空）。只影响有缓存写入的请求。
- **`tiers`**：`[{threshold: 32000, ratio: 1.5}]` = 输入超过 32k token 时 `model_ratio` 变 1.5。`price` 命令只按基础倍率算。
- **按次计费**（`quota_type: 1`，图片 / 视频）：`model_price` 是每次的平台价，不走 token 公式。
- **`catalog_version`** 在多个后端间来回跳（5189 / 12 / 17 / 1580 …），不是单调递增，不能当调价信号。要判断调价：`snapshot` 留底，下次 diff 价格字段。

## 估算

- 一条 20 token 输入 + 8 token 输出的最小探针：`quota ≈ mr × gr × (20 + 8 × completion_ratio)`。
  Claude Opus 在 `cc-expensive`（mr 2 × gr 2.8）≈ 336 quota ≈ ¥0.0007；在 `claude-officially` ≈ ¥0.0017。
- 上游偶发注入 ~25k token 系统提示并计费（见 `groups.md`），贵模型一次探针可能 ¥0.5+，所以 `probe` 必须 `--apply` 并先给用户看预估。
