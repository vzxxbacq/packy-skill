# 测试提问

用来验证 skill 能触发、agent 用对命令、回答符合硬规则。每条后面是期望行为。

1. **"packy 还剩多少钱？"** → `balance`；回答先 ¥ 后 ≈$；不把余额说成美元。
2. **"claude-opus-5-5 现在走哪个分组最便宜？"** → `cheapest claude-opus-5-5`；不推荐 `cc`；提到 `cc-sale` 缓存风险。
3. **"按这个速度余额能撑几天？"** → `balance` + `spend --since 7d --by day`，算日耗和天数。
4. **"昨天哪把 key 花得最多？"** → `spend --since 24h --by key`（或 `--since 2026-xx-xx --until ...`）。
5. **"刚才那次 opus 调用为什么扣了 ¥2.6？"** → `logs --since 1h -m claude-opus-5-5 --verify`；解释缓存写 / 注入 token；不拿余额差推算。
6. **"帮我建一把 cc-expensive 的 key，限额 50 块"** → 先 dry-run 展示 body，确认后 `--apply`；输出 `PACKY_KEY_CC_EXPENSIVE=...` 让用户写 .env；不在后续对话重复明文。
7. **"deepseek 现在是高峰加价吗？"** → `peak`；解释窗口（工作日 9-12 / 14-18 Asia/Shanghai ×2）。
8. **"cc 分组能不能拿来做中转？"** → 明确说不能（禁止代理 / 二次分发，403 + 反滥用），建议 `cc-expensive`。
9. **"验证一下 cc-sale 的 sonnet 实扣价对不对"** → `probe claude-sonnet-5-5 -g cc-sale` 先 dry-run 给预估，用户确认再 `--apply`；读 `log_matches_pricing`。
10. **"价格表是不是调价了？"** → 不看 `catalog_version`；用 `snapshot` 留底 + diff 价格字段。
11. **"用 python requests 直接调 /api/user/self 为什么 403？"** → 指出 Cloudflare 拦默认 UA（error 1010），加自定义 User-Agent。
12. **"把 test-2 这把 key 停了"** → `key disable --name test-2`，dry-run → `--apply`；建议先看 `last used`。
