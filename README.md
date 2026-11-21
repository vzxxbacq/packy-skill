# packy-skill

给 AI agent（Claude Code / Codex / 任何支持 [Agent Skills](https://agentskills.io) 的客户端）用的
[PackyAPI](https://www.packyapi.com) 账户读取与管理技能：查余额、查各分组真实价格、找最便宜分组、
看扣费日志和消费汇总、管理 API key、发探针核对真实扣费。

只依赖 PackyAPI 自己的接口（`/api/pricing`、`/api/user/self`、`/api/token/`、`/api/log/self`），
Python 3.10+ 标准库，零第三方依赖。

## 安装

```bash
git clone https://github.com/vzxxbacq/packy-skill
cd packy-skill
./scripts/install.sh            # 软链到 ~/.claude/skills/packy，并把 packyctl 放进 ~/.local/bin
```

或者直接把目录拷到任何客户端的 skills 目录。

## 配置

```bash
cp .env.example ~/.config/packy/.env   # 或放在项目目录 ./.env，或 PACKY_ENV=/path/to/.env
```

| 变量 | 必填 | 说明 |
|---|---|---|
| `PACKY_ACCESS_TOKEN` | 读账户 / 日志 / key 时必填 | 控制台 → 个人设置 → 系统访问令牌 |
| `PACKY_USER_ID` | 同上 | 控制台个人设置里的用户 ID |
| `PACKY_KEY_<GROUP>` | `probe` / `cheapest` 用 | 各分组的 API key，分组名大写、`-` 换 `_`，如 `PACKY_KEY_CC_EXPENSIVE` |
| `CNY_PER_USD` | 否 | 平台金额是人民币，换算美元用的汇率，默认 7 |
| `SELL_MARKUP` | 否 | 如果你在二次转售，`price --json` 多给一列卖价 = 买价 × (1 + markup) |
| `PACKY_ADMIN_BASE` / `PACKY_BASE` | 否 | 默认 `https://www.packyapi.com` / `https://cf.api.fan` |

`/api/pricing` 相关命令（`groups` `models` `price` `cheapest` `peak` `snapshot`）不需要 token。

## 用法

```bash
packyctl balance
packyctl price claude-opus-5-5
packyctl cheapest claude-opus-5-5
packyctl spend --since 7d --by key
packyctl logs --since 2h -m claude-opus-5-5 --verify
packyctl keys
packyctl key create --name test --group deepseek-sale --quota-cny 5 --apply
packyctl probe deepseek-v4-flash -g deepseek-sale --apply
packyctl --json balance          # 结构化输出
```

写操作（`key *`、`probe`）默认 dry-run，加 `--apply` 才执行。

## 目录

```
SKILL.md               agent 入口：触发条件、决策表、硬规则
references/api.md      接口字段、分页、鉴权
references/pricing.md  扣费公式、单位换算
references/groups.md   分组含义与已知坑
references/runbooks.md 常见任务处置流程
scripts/packyctl.py    CLI
evals/prompts.md       测试提问
```

## 关键事实

- 平台所有金额是**人民币**，页面的 "$" 只是符号：`ratio 1 = ¥2/M token`，`500000 quota = ¥1`。
- 管理接口只在 `www.packyapi.com`，模型调用只走 `cf.api.fan`。
- 请求必须带非默认 User-Agent，否则 Cloudflare 403。
- `cc` 分组禁止代理调用。
- 新建 key 的明文只在创建响应里出现一次。

详见 `SKILL.md` 和 `references/`。

## License

MIT
