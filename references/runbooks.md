# 处置流程

每个流程先读、后改；改动先 dry-run 给用户看，确认后 `--apply`。

## A. "余额还有多少 / 还能撑几天"

```bash
packyctl balance
packyctl spend --since 7d --by day
```

日耗 = 近 7 天 ¥ / 7；可撑天数 = 余额 / 日耗。报告写 ¥ 并括号 ≈$。
若日耗异常，`spend --since 24h --by key` 看哪把 key 在烧，再 `--by model`。

## B. "这个模型哪个分组最划算"

```bash
packyctl price MODEL           # 全部分组
packyctl cheapest MODEL        # 只看本地有 key、排除 cc、排除停用
```

注意：
- `cheapest` 默认 input/output 权重 1:1。Claude Code 类流量 80%+ 是缓存读，用 `--weights` 或直接看 `¥cache` 列。
- codex 系要加固定注入开销（`groups.md`）。
- 看到 ⚡ 表示此刻高峰加价，`packyctl peak` 看窗口。
- 最便宜 ≠ 最稳：`cc-sale` 官方标注缓存可能异常，`cc-expensive` 有 haiku 注入膨胀。

## C. "扣费对不对 / 为什么这么贵"

```bash
packyctl logs --since 2h -m MODEL --verify
```

1. `expected` 列 ✗ → 公式与日志倍率不符，极少见，记录下来。
2. 公式吻合但 `in` 异常大（几万 token，而请求很短）→ 上游通道注入系统提示（`groups.md`），不是用户的问题。建议换分组或避开该模型。
3. `cache` 列为 0 而请求本应命中缓存 → 该分组缓存未生效，实际比标价贵很多（`cache_ratio 0.05` 变 1.0）。
4. 比较不同分组实扣：`spend --since 24h --by group`，看 `hit` 列（缓存读 / 总输入）。

## D. "某分组用不了 / 报 403"

1. 先问用什么客户端：`cc` 分组只放行 Claude Code 官方客户端，代理一律 403，无解，换 `cc-sale` / `cc-expensive`。
2. 403 `error code: 1010` → Cloudflare 拦 UA，加自定义 User-Agent。
3. 403 提到"监控探针 / 重复" → 请求体重复被判探针，换内容，等一会再试。
4. 其它：`packyctl probe MODEL -g GROUP --apply`（先看 dry-run 预估），看 `error` 字段；`packyctl groups` 看分组是否 `inactive`；`packyctl models -g GROUP` 看模型是否还在该分组。
5. 上游多通道随机，连试 2~3 次（内容不同）再下结论。

## E. "建一把 key"

```bash
packyctl key create --name router-cc-expensive --group cc-expensive            # 无限额度
packyctl key create --name test --group deepseek-sale --quota-cny 5 --expires 7 # 限 ¥5、7 天
```

1. 先 dry-run 给用户看 body。
2. `--apply` 后，响应里的明文 key **只出现一次**：输出 `PACKY_KEY_<GROUP>=...` 让用户写进 `.env`，不要在对话里重复完整 key。
3. `packyctl keys` 确认出现在列表。

## F. "停掉 / 删掉 / 改额度"

```bash
packyctl key disable --name old-key --apply
packyctl key update --name test --quota-cny 10 --apply
packyctl key update --id 123 --unlimited --expires 0 --apply   # 0 = 永不过期
packyctl key delete --id 123 --apply
```

删除不可恢复，删前 `keys` 看 `last used`，还在用的先 `disable` 观察。

## G. "价格变了吗"

```bash
packyctl snapshot -o snap-$(date +%F).json
diff <(jq -S '{group_ratio, model_group_ratio, data: [.data[] | {model_name, model_ratio, completion_ratio, cache_ratio}]}' old.json) \
     <(jq -S '...同上...' new.json)
```

不要看 `catalog_version`，它在后端间跳动。

## H. "验证某分组现在的真实价"

```bash
packyctl probe MODEL -g GROUP          # dry-run，看预估
packyctl probe MODEL -g GROUP --apply  # 发请求，等 15s，拿日志比对
```

输出 `log_matches_pricing: true` = 实扣与价格表吻合；`input_inflated: true` = 被注入 token。
贵模型一次探针可能 ¥0.5+（注入时），先告诉用户。
