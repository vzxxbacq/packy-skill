# PackyAPI 接口速查

PackyAPI 是 [new-api](https://github.com/QuantumNous/new-api) 的分支，管理接口与 new-api 一致。
下面所有字段都是 2026-11 实测过的。

## 域名

| 用途 | 域名 | 说明 |
|---|---|---|
| 管理接口 `/api/*` | `https://www.packyapi.com` | 只有主站有；`cf.api.fan/api/*` 返回 404 |
| 模型调用 `/v1/*` | `https://cf.api.fan` | 官方公告：主站对模型调用做区域限制，调用改走这个（或 `slb-v1.api.fan`） |

## 必带请求头

```
User-Agent: packy-skill/0.1        # 任意非默认 UA；缺了或用 Python-urllib / python-requests 会被 Cloudflare 拦：403 "error code: 1010"
Authorization: Bearer <PACKY_ACCESS_TOKEN>   # 控制台 → 个人设置 → 系统访问令牌（/api/pricing 不需要）
New-Api-User: <PACKY_USER_ID>                # 控制台个人设置里的用户 ID
Content-Type: application/json
```

响应统一包一层：`{"success": true, "message": "", "data": ...}`；失败 `success: false` + `message`。

## 1. `GET /api/pricing`（公开，无需鉴权）

| 字段 | 含义 |
|---|---|
| `data[]` | 模型列表，每条：`model_name`、`model_ratio`、`completion_ratio`、`cache_ratio`、`cache_creation_ratio_5m`、`enable_groups[]`、`supported_endpoint_types[]`、`price_currency`（仅标签）、`quota_type`（0 按 token，1 按次）、`model_price`（按次价）、`tiers[]`（分段倍率 `{threshold, ratio}`，按输入长度） |
| `group_ratio{group: ratio}` | 分组倍率 |
| `model_group_ratio{group: {model: ratio}}` | 分组对单个模型的覆盖倍率，**优先于** `group_ratio` |
| `model_group_endpoints{group: {model: [endpoints]}}` | 分组下该模型允许的协议，覆盖 `supported_endpoint_types` |
| `peak_pricing` | 高峰加价规则：`enabled`、`rules[]{timezone, windows, weekdays, models, factor}` |
| `peak_active{model: factor}` | 此刻生效的高峰加价 |
| `usable_group{group: desc}` | 分组中文说明 |
| `auto_groups[]` | `auto` 分组解析顺序（目前 `["cc"]`） |
| `inactive_groups[]` | 已停用分组 |
| `supported_endpoint{type: {path, method}}` | 协议 → 路径 |
| `catalog_version` | 版本号，**在多后端间跳动，不能当调价信号** |

## 2. `GET /api/user/self`

`data.quota` 剩余额度（**500000 = ¥1**），`data.used_quota` 累计消费，`data.request_count`，`data.group` 用户默认分组。
响应里还有邮箱、aff 等个人信息，不要原样打印。

## 3. `GET /api/user/self/groups`

`data{group: {desc, ratio, ratio_range: [min, max], inactive, vendors[]}}`。
`ratio_range` 的 min≠max 表示该分组对部分模型有 `model_group_ratio` 覆盖。

## 4. `/api/token/`（API key）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/token/?p=1&size=100` | 分页列表，`data.{items[], total, page, page_size}`。**`page_size` 服务端封顶**，按已取条数 ≥ `total` 判断翻完，不要按 `p × 请求的 page_size` |
| GET | `/api/token/{id}` | 单条 |
| POST | `/api/token/` | 创建，body：`{name, group, remain_quota, unlimited_quota, expired_time(-1 永不 / unix 秒), model_limits_enabled, model_limits, allow_ips}`。响应 `data.{id, key}` —— **明文 key 只在这里出现一次** |
| PUT | `/api/token/` | 整体更新：先 GET 单条拿完整对象，改字段后原样 PUT。改 `status` 用这个**不生效** |
| PUT | `/api/token/?status_only=true` | 启停：body `{id, status}`，1 启用 2 停用 |
| DELETE | `/api/token/{id}` | 删除；之后 GET 返回 `record not found` |

列表每条字段：`id, name, group, status(1/2), key_prefix, key_hint(后 8 位), used_quota, remain_quota, unlimited_quota,
expired_time, accessed_time, created_time, allow_ips, group_mode, quota_limit, quota_reset_period`。
`group` 为空 = 跟随用户默认分组。

## 5. `/api/log/self`（扣费日志）

`GET /api/log/self?p=1&page_size=100&type=2&start_timestamp=&end_timestamp=&group=&model_name=&token_name=`

- `type`：2 = 消费（扣费）记录；0 = 全部（含充值 / 管理）。
- `page_size` 同样封顶 100；`start_timestamp` 含、`end_timestamp` 含，都是 unix 秒。
- 筛选参数 `group` / `model_name` / `token_name` 实测有效，服务端过滤。
- 每条：`created_at, token_name, token_id, group, model_name, quota, prompt_tokens, completion_tokens, use_time(秒), is_stream, user_agent, other(JSON 字符串)`。
- `other` 解析后：`model_ratio, group_ratio（已是有效倍率）, model_group_ratio（有覆盖时）, ratio_source("model"=覆盖 / "group"), completion_ratio, cache_ratio, cache_tokens, cache_creation_ratio, cache_creation_tokens, claude(true=Anthropic 协议), request_path, frt`。
- **token 口径**：`claude=true` 时 `prompt_tokens` 不含 `cache_tokens`；否则含，要减掉。
- `quota = model_ratio × group_ratio × (prompt + completion×completion_ratio + cache×cache_ratio + cache_write×cache_creation_ratio)`，取整。

`GET /api/log/self/stat?type=2&start_timestamp=&end_timestamp=&group=&model_name=&token_name=` → `data.{quota, rpm, tpm}`，服务端聚合，用来核对逐条求和。

## 6. 模型调用（探针用）

| 协议 | 路径 | 鉴权 | 最小请求体 |
|---|---|---|---|
| `anthropic` | `POST /v1/messages` | `x-api-key: <key>`，`anthropic-version: 2023-06-01` | `{model, max_tokens: 8, messages:[{role:user, content}]}` |
| `openai` | `POST /v1/chat/completions` | `Authorization: Bearer <key>` | `{model, messages, max_tokens: 8}`；新模型报错要求 `max_completion_tokens` 就换字段 |
| `openai-response` | `POST /v1/responses` | `Authorization: Bearer <key>` | `{model, input:[{role:user, content}], max_output_tokens: 16}` |

响应 usage 字段：Anthropic `input_tokens / output_tokens / cache_read_input_tokens / cache_creation_input_tokens`；
OpenAI `prompt_tokens(含缓存) / completion_tokens / prompt_tokens_details.cached_tokens`；
Responses `input_tokens(含缓存) / output_tokens / input_tokens_details.cached_tokens`。

探针后等 ~15 秒日志才落库，再按 `model_name` + `start_timestamp` 查 `/api/log/self` 核对。
