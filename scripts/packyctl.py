#!/usr/bin/env python3
"""packyctl — PackyAPI 账户的读取与管理 CLI（纯标准库，Python 3.10+）。

只用 PackyAPI 自己的接口：
  管理接口  {PACKY_ADMIN_BASE}/api/*   默认 https://www.packyapi.com  （pricing / user / token / log）
  模型调用  {PACKY_BASE}/v1/*          默认 https://cf.api.fan        （仅 probe 用）

鉴权来自 .env（查找顺序：$PACKY_ENV → ./.env → ~/.config/packy/.env），变量见 .env.example。
所有会改动账户或花钱的子命令默认 dry-run，加 --apply 才真正执行。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "0.1.0"
UA = f"packy-skill/{VERSION}"  # 必须带非默认 UA，否则 Cloudflare 返回 403 error 1010
QUOTA_PER_CNY = 500_000  # 500000 quota = ¥1
CNY_PER_RATIO_M = 2.0  # ratio 1 = ¥2 / M input token
ENDPOINT_PREF = ["anthropic", "openai", "openai-response"]


# ---------- env / http ----------


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    candidates = [os.environ.get("PACKY_ENV"), ".env", str(Path.home() / ".config" / "packy" / ".env")]
    for c in candidates:
        if c and Path(c).is_file():
            for line in Path(c).read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            env["_env_file"] = c
            break
    for k, v in os.environ.items():
        if k.startswith("PACKY_") or k in ("CNY_PER_USD", "SELL_MARKUP"):
            env[k] = v
    env.setdefault("PACKY_ADMIN_BASE", "https://www.packyapi.com")
    env.setdefault("PACKY_BASE", "https://cf.api.fan")
    env.setdefault("CNY_PER_USD", "7")
    env.setdefault("SELL_MARKUP", "0")
    return env


def group_keys(env: dict[str, str]) -> dict[str, str]:
    """PACKY_KEY_<GROUP> → {group: key}；大写下划线还原成小写短横线。"""
    out = {}
    for k, v in env.items():
        if k.startswith("PACKY_KEY_") and v and not v.endswith("..."):
            out[k[len("PACKY_KEY_"):].lower().replace("_", "-")] = v
    return out


class Api:
    def __init__(self, env: dict[str, str]):
        self.env = env
        self.admin = env["PACKY_ADMIN_BASE"].rstrip("/")
        self.base = env["PACKY_BASE"].rstrip("/")

    def _auth(self) -> dict[str, str]:
        tok, uid = self.env.get("PACKY_ACCESS_TOKEN"), self.env.get("PACKY_USER_ID")
        if not tok or not uid:
            die("需要 PACKY_ACCESS_TOKEN 和 PACKY_USER_ID（控制台 → 个人设置 → 系统访问令牌）")
        return {"Authorization": f"Bearer {tok}", "New-Api-User": str(uid)}

    def request(self, method: str, url: str, body=None, headers=None, auth=True, timeout=60) -> dict:
        h = {"User-Agent": UA, "Content-Type": "application/json"}
        if auth:
            h.update(self._auth())
        if headers:
            h.update(headers)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, headers=h, method=method, data=data)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode()
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")[:400]
            raise RuntimeError(f"HTTP {e.code} {method} {url}: {text}") from None
        try:
            d = json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"非 JSON 响应 {method} {url}: {text[:200]}") from None
        if isinstance(d, dict) and d.get("success") is False:
            raise RuntimeError(f"{method} {url}: {d.get('message') or d}")
        return d

    def get(self, path: str, params: dict | None = None, auth=True) -> dict:
        q = ("?" + urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})) if params else ""
        return self.request("GET", self.admin + path + q, auth=auth)

    # --- 具体接口 ---
    def pricing(self) -> dict:
        return self.get("/api/pricing", auth=False)

    def user(self) -> dict:
        return self.get("/api/user/self")["data"]

    def user_groups(self) -> dict:
        return self.get("/api/user/self/groups")["data"]

    def tokens(self) -> list[dict]:
        out, p = [], 1
        while True:
            d = self.get("/api/token/", {"p": p, "size": 100})["data"]
            items = d.get("items") or []
            out += items
            if not items or len(out) >= d.get("total", 0):
                return out
            p += 1

    def token(self, tid: int) -> dict:
        return self.get(f"/api/token/{tid}")["data"]

    def logs(self, start_ts: int, end_ts: int | None = None, group=None, model=None, token_name=None,
             log_type=2, page_size=200, limit: int | None = None) -> list[dict]:
        out, p = [], 1
        while True:
            params = {"p": p, "page_size": page_size, "type": log_type, "start_timestamp": start_ts,
                      "end_timestamp": end_ts, "group": group, "model_name": model, "token_name": token_name}
            d = self.get("/api/log/self", params)["data"]
            items = d if isinstance(d, list) else d.get("items") or []
            for it in items:
                try:
                    it["other"] = json.loads(it.get("other") or "{}")
                except json.JSONDecodeError:
                    it["other"] = {}
                out.append(it)
            total = None if isinstance(d, list) else d.get("total")
            # 服务端会把 page_size 封顶（实测 100），按实际返回条数判断是否翻完
            if not items or total is None or len(out) >= total or (limit and len(out) >= limit):
                break
            p += 1
        out = [x for x in out if x.get("created_at", 0) >= start_ts]
        return out[:limit] if limit else out

    def log_stat(self, start_ts: int, end_ts: int | None = None, group=None, model=None, token_name=None) -> dict:
        return self.get("/api/log/self/stat", {"type": 2, "start_timestamp": start_ts, "end_timestamp": end_ts,
                                               "group": group, "model_name": model, "token_name": token_name})["data"]


# ---------- 价格 ----------


class Pricing:
    def __init__(self, raw: dict):
        self.raw = raw
        self.models = {m["model_name"]: m for m in raw.get("data", [])}

    def group_ratio(self, group: str, model: str) -> float | None:
        override = (self.raw.get("model_group_ratio") or {}).get(group, {}).get(model)
        if override is not None:
            return float(override)
        gr = (self.raw.get("group_ratio") or {}).get(group)
        return None if gr is None else float(gr)

    def peak_factor(self, model: str) -> float:
        return float((self.raw.get("peak_active") or {}).get(model, 1))

    def endpoints(self, group: str, model: str) -> list[str]:
        mge = (self.raw.get("model_group_endpoints") or {}).get(group, {})
        if model in mge:
            return list(mge[model])
        return list(self.models[model].get("supported_endpoint_types") or [])

    def ratios(self, group: str, model: str) -> dict[str, float]:
        m = self.models[model]
        return {
            "model_ratio": float(m.get("model_ratio") or 0),
            "group_ratio": self.group_ratio(group, model) or 0.0,
            "peak": self.peak_factor(model),
            "completion_ratio": float(m.get("completion_ratio") or 1),
            "cache_ratio": float(m.get("cache_ratio") or 1),
            "cache_creation_ratio": float(m.get("cache_creation_ratio_5m") or m.get("cache_creation_ratio") or 1),
        }


def cny_per_million(r: dict[str, float]) -> dict[str, float]:
    base = r["model_ratio"] * r["group_ratio"] * r.get("peak", 1) * CNY_PER_RATIO_M
    return {"input": base, "output": base * r["completion_ratio"],
            "cache_read": base * r["cache_ratio"], "cache_write": base * r["cache_creation_ratio"]}


def quota_for(usage: dict, r: dict[str, float]) -> float:
    """new-api 扣费公式（quota = model_ratio × group_ratio × 加权 token，0.002/1000×500000 = 1）。"""
    weighted = (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0) * r["completion_ratio"]
                + usage.get("cache_tokens", 0) * r["cache_ratio"]
                + usage.get("cache_creation_tokens", 0) * r["cache_creation_ratio"])
    return r["model_ratio"] * r["group_ratio"] * r.get("peak", 1) * weighted


def log_usage(lg: dict) -> dict:
    """日志 token 口径：Anthropic 协议 prompt_tokens 不含缓存；OpenAI/Responses 含缓存要减掉。"""
    o = lg.get("other") or {}
    cache = int(o.get("cache_tokens") or 0)
    prompt = int(lg.get("prompt_tokens") or 0)
    if not o.get("claude"):
        prompt = max(prompt - cache, 0)
    return {"prompt_tokens": prompt, "completion_tokens": int(lg.get("completion_tokens") or 0),
            "cache_tokens": cache, "cache_creation_tokens": int(o.get("cache_creation_tokens") or 0)}


def log_ratios(lg: dict) -> dict[str, float]:
    o = lg.get("other") or {}
    gr = o.get("model_group_ratio")
    if not gr or gr <= 0:
        gr = o.get("group_ratio") or 0
    return {"model_ratio": float(o.get("model_ratio") or 0), "group_ratio": float(gr), "peak": 1.0,
            "completion_ratio": float(o.get("completion_ratio") or 1), "cache_ratio": float(o.get("cache_ratio") or 1),
            "cache_creation_ratio": float(o.get("cache_creation_ratio") or 1)}


# ---------- 输出 ----------


def die(msg: str, code: int = 2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def cny(v: float, env) -> str:
    return f"¥{v:,.2f} (≈${v / float(env['CNY_PER_USD']):,.2f})"


def ts(t: int) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def parse_since(s: str) -> int:
    """'24h' / '7d' / '30m' / '2026-01-31' / '2026-01-31T10:00' / unix 秒 → 起始时间戳。"""
    s = s.strip()
    if s.isdigit() and len(s) >= 9:
        return int(s)
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    if s[-1] in units and s[:-1].replace(".", "").isdigit():
        return int(time.time() - float(s[:-1]) * units[s[-1]])
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(dt.datetime.strptime(s, fmt).timestamp())
        except ValueError:
            pass
    die(f"无法解析时间：{s}")


def table(rows: list[list], headers: list[str]):
    if not rows:
        print("(empty)")
        return
    rows = [[str(c) for c in r] for r in rows]
    w = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * w[i] for i in range(len(headers))))
    for r in rows:
        print("  ".join(r[i].ljust(w[i]) for i in range(len(headers))))


def emit(args, data, render):
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        render()


# ---------- 读 ----------


def cmd_balance(api: Api, args):
    u = api.user()
    bal = u["quota"] / QUOTA_PER_CNY
    used = u.get("used_quota", 0) / QUOTA_PER_CNY
    data = {"balance_cny": round(bal, 4), "balance_usd": round(bal / float(api.env["CNY_PER_USD"]), 4),
            "used_total_cny": round(used, 4), "request_count": u.get("request_count"), "user_group": u.get("group"),
            "cny_per_usd": float(api.env["CNY_PER_USD"])}
    emit(args, data, lambda: print(f"余额  {cny(bal, api.env)}\n累计消费 {cny(used, api.env)}，请求数 {u.get('request_count')}"))


def cmd_groups(api: Api, args):
    pr = Pricing(api.pricing())
    ug = {}
    try:
        ug = api.user_groups()
    except (RuntimeError, SystemExit):
        pass
    keys = group_keys(api.env)
    inactive = set(pr.raw.get("inactive_groups") or [])
    names = sorted(set(pr.raw.get("group_ratio") or {}) | set(ug))
    rows, data = [], []
    for g in names:
        info = ug.get(g, {})
        rr = info.get("ratio_range")
        n_models = len([m for m, e in pr.models.items() if g in (e.get("enable_groups") or [])])
        row = {"group": g, "ratio": (pr.raw.get("group_ratio") or {}).get(g, info.get("ratio")),
               "ratio_range": rr, "models": n_models, "inactive": g in inactive or bool(info.get("inactive")),
               "has_key": g in keys, "desc": (info.get("desc") or (pr.raw.get("usable_group") or {}).get(g) or "").strip()}
        data.append(row)
        rows.append([g, row["ratio"], f"{rr[0]}~{rr[1]}" if rr and rr[0] != rr[1] else "", n_models,
                     "✗" if row["inactive"] else "", "✓" if row["has_key"] else "", row["desc"][:40]])
    emit(args, data, lambda: table(rows, ["group", "ratio", "override", "models", "inactive", "key", "desc"]))


def cmd_models(api: Api, args):
    pr = Pricing(api.pricing())
    rows, data = [], []
    for name, m in sorted(pr.models.items()):
        groups = m.get("enable_groups") or []
        eps = m.get("supported_endpoint_types") or []
        if args.group and args.group not in groups:
            continue
        if args.endpoint and args.endpoint not in eps:
            continue
        if args.search and args.search.lower() not in name.lower():
            continue
        if not args.all and m.get("quota_type", 0) != 0:
            continue
        row = {"model": name, "model_ratio": m.get("model_ratio"), "completion_ratio": m.get("completion_ratio"),
               "cache_ratio": m.get("cache_ratio"), "quota_type": m.get("quota_type", 0),
               "price_currency": m.get("price_currency"), "tiers": m.get("tiers"), "endpoints": eps, "groups": groups}
        data.append(row)
        rows.append([name, m.get("model_ratio"), m.get("completion_ratio"), m.get("cache_ratio"),
                     "per-call" if m.get("quota_type") else "token", "tiered" if m.get("tiers") else "",
                     ",".join(eps), ",".join(groups)])
    emit(args, data, lambda: table(rows, ["model", "ratio", "out×", "cache×", "bill", "tiers", "endpoints", "groups"]))


def price_rows(api: Api, pr: Pricing, model: str, groups: list[str]) -> list[dict]:
    keys = group_keys(api.env)
    inactive = set(pr.raw.get("inactive_groups") or [])
    markup = float(api.env.get("SELL_MARKUP") or 0)
    usd = float(api.env["CNY_PER_USD"])
    out = []
    for g in groups:
        r = pr.ratios(g, model)
        p = cny_per_million(r)
        row = {"group": g, "model": model, "ratios": r, "cny_per_m": {k: round(v, 4) for k, v in p.items()},
               "usd_per_m": {k: round(v / usd, 4) for k, v in p.items()},
               "peak": r["peak"] != 1, "endpoints": pr.endpoints(g, model), "has_key": g in keys,
               "inactive": g in inactive, "override": pr.group_ratio(g, model) != (pr.raw.get("group_ratio") or {}).get(g)}
        if markup:
            row["sell_usd_per_m"] = {k: round(v / usd * (1 + markup), 4) for k, v in p.items()}
        out.append(row)
    return out


def cmd_price(api: Api, args):
    pr = Pricing(api.pricing())
    if args.model not in pr.models:
        cands = [m for m in pr.models if args.model.lower() in m.lower()]
        die(f"价格表没有 {args.model}" + (f"，相近：{', '.join(cands[:10])}" if cands else ""))
    m = pr.models[args.model]
    groups = [args.group] if args.group else list(m.get("enable_groups") or [])
    if args.group and args.group not in (m.get("enable_groups") or []):
        die(f"{args.model} 不在分组 {args.group}（可用：{', '.join(m.get('enable_groups') or [])}）")
    data = price_rows(api, pr, args.model, groups)
    data.sort(key=lambda r: r["cny_per_m"]["input"] + r["cny_per_m"]["output"])

    def render():
        print(f"{args.model}  model_ratio={m.get('model_ratio')} out×{m.get('completion_ratio')} cache×{m.get('cache_ratio')}"
              f"  {'按次计费' if m.get('quota_type') else ''}{'  分段倍率:' + json.dumps(m.get('tiers')) if m.get('tiers') else ''}")
        print("价格单位：¥/M token（平台标价，页面显示的 $ 实为人民币） | $/M = ÷ CNY_PER_USD")
        rows = []
        for r in data:
            p, u = r["cny_per_m"], r["usd_per_m"]
            flags = "".join(["⚡" if r["peak"] else "", "✗" if r["inactive"] else "", "✓" if r["has_key"] else "", "*" if r["override"] else ""])
            rows.append([r["group"], r["ratios"]["group_ratio"], f"{p['input']:.3f}", f"{p['output']:.3f}", f"{p['cache_read']:.4f}",
                         f"{u['input']:.3f}", f"{u['output']:.3f}", ",".join(r["endpoints"]), flags])
        table(rows, ["group", "g.ratio", "¥in", "¥out", "¥cache", "$in", "$out", "endpoints", "flags"])
        print("flags: ✓有key  ✗分组停用  ⚡高峰加价生效  *model_group_ratio 覆盖")
    emit(args, data, render)


def cmd_cheapest(api: Api, args):
    pr = Pricing(api.pricing())
    if args.model not in pr.models:
        die(f"价格表没有 {args.model}")
    groups = list(pr.models[args.model].get("enable_groups") or [])
    rows = price_rows(api, pr, args.model, groups)
    rows = [r for r in rows if not r["inactive"] and (args.include_no_key or r["has_key"])
            and (args.include_cc or r["group"] != "cc") and r["ratios"]["group_ratio"] > 0]
    w_in, w_out = args.weights
    for r in rows:
        r["score_cny_per_m"] = round(r["cny_per_m"]["input"] * w_in + r["cny_per_m"]["output"] * w_out, 4)
    rows.sort(key=lambda r: r["score_cny_per_m"])
    if not rows:
        die("没有候选分组（试试 --include-no-key）")
    emit(args, rows, lambda: table([[i + 1, r["group"], r["score_cny_per_m"], f"{r['cny_per_m']['input']:.3f}",
                                     f"{r['cny_per_m']['output']:.3f}", "⚡" if r["peak"] else ""] for i, r in enumerate(rows)],
                                   ["#", "group", "score ¥/M", "¥in", "¥out", "peak"]))


def cmd_peak(api: Api, args):
    raw = api.pricing()
    data = {"peak_pricing": raw.get("peak_pricing"), "peak_active": raw.get("peak_active") or {}}

    def render():
        print("当前生效：", json.dumps(data["peak_active"], ensure_ascii=False) or "无")
        print("规则：", json.dumps(data["peak_pricing"], ensure_ascii=False, indent=2))
    emit(args, data, render)


def cmd_keys(api: Api, args):
    toks = api.tokens()
    data = []
    for t in toks:
        data.append({"id": t["id"], "name": t["name"], "group": t.get("group"), "status": t.get("status"),
                     "hint": f"{t.get('key_prefix', 'sk')}-…{t.get('key_hint', '')}",
                     "used_cny": round(t.get("used_quota", 0) / QUOTA_PER_CNY, 4),
                     "remain_cny": None if t.get("unlimited_quota") else round(t.get("remain_quota", 0) / QUOTA_PER_CNY, 4),
                     "unlimited": bool(t.get("unlimited_quota")), "expired_time": t.get("expired_time"),
                     "accessed_time": t.get("accessed_time")})
    data.sort(key=lambda x: (x["group"] or "", x["name"]))
    emit(args, data, lambda: table([[d["id"], d["name"], d["group"], "on" if d["status"] == 1 else "off", d["hint"],
                                     f"{d['used_cny']:.2f}", "∞" if d["unlimited"] else f"{d['remain_cny']:.2f}",
                                     "never" if d["expired_time"] == -1 else ts(d["expired_time"]),
                                     ts(d["accessed_time"]) if d["accessed_time"] else ""] for d in data],
                                   ["id", "name", "group", "status", "key", "used ¥", "remain ¥", "expires", "last used"]))


def summarize(logs: list[dict], by: str) -> list[dict]:
    agg: dict[str, dict] = {}
    for lg in logs:
        if by == "day":
            k = dt.datetime.fromtimestamp(lg["created_at"]).strftime("%Y-%m-%d")
        elif by == "hour":
            k = dt.datetime.fromtimestamp(lg["created_at"]).strftime("%Y-%m-%d %H:00")
        elif by == "key":
            k = lg.get("token_name") or "?"
        elif by == "model":
            k = lg.get("model_name") or "?"
        else:
            k = lg.get("group") or "?"
        a = agg.setdefault(k, {"key": k, "requests": 0, "quota": 0, "prompt": 0, "completion": 0, "cache": 0, "cache_write": 0})
        u = log_usage(lg)
        a["requests"] += 1
        a["quota"] += lg.get("quota", 0)
        a["prompt"] += u["prompt_tokens"]
        a["completion"] += u["completion_tokens"]
        a["cache"] += u["cache_tokens"]
        a["cache_write"] += u["cache_creation_tokens"]
    out = []
    for a in sorted(agg.values(), key=lambda x: -x["quota"]):
        a["cny"] = round(a["quota"] / QUOTA_PER_CNY, 4)
        tot_in = a["prompt"] + a["cache"]
        a["cache_hit"] = round(a["cache"] / tot_in, 3) if tot_in else None
        out.append(a)
    return out


def cmd_logs(api: Api, args):
    start = parse_since(args.since)
    end = parse_since(args.until) if args.until else None
    logs = api.logs(start, end, group=args.group, model=args.model, token_name=args.key, limit=args.limit)
    rows, data, mismatches = [], [], 0
    for lg in logs:
        u = log_usage(lg)
        rec = {"time": ts(lg["created_at"]), "created_at": lg["created_at"], "key": lg.get("token_name"), "group": lg.get("group"),
               "model": lg.get("model_name"), "quota": lg.get("quota"), "cny": round(lg.get("quota", 0) / QUOTA_PER_CNY, 5),
               "usage": u, "latency_s": lg.get("use_time"), "stream": lg.get("is_stream"), "ratios": log_ratios(lg),
               "request_path": (lg.get("other") or {}).get("request_path"), "user_agent": lg.get("user_agent")}
        if args.verify:
            exp = quota_for(u, rec["ratios"])
            rec["expected_quota"] = round(exp, 2)
            rec["mismatch"] = abs(exp - lg.get("quota", 0)) > max(1.0, 0.02 * lg.get("quota", 0))
            mismatches += rec["mismatch"]
        data.append(rec)
        rows.append([rec["time"], rec["key"], rec["group"], rec["model"], rec["quota"], u["prompt_tokens"], u["completion_tokens"],
                     u["cache_tokens"], u["cache_creation_tokens"], rec["latency_s"]]
                    + ([f"{rec['expected_quota']}{' ✗' if rec['mismatch'] else ''}"] if args.verify else []))

    def render():
        table(rows, ["time", "key", "group", "model", "quota", "in", "out", "cache", "cache_w", "s"] + (["expected"] if args.verify else []))
        tot = sum(l.get("quota", 0) for l in logs)
        print(f"\n{len(logs)} 条，合计 {tot} quota = {cny(tot / QUOTA_PER_CNY, api.env)}" + (f"，公式不符 {mismatches} 条" if args.verify else ""))
    emit(args, data, render)


def cmd_spend(api: Api, args):
    start = parse_since(args.since)
    end = parse_since(args.until) if args.until else None
    logs = api.logs(start, end, group=args.group, model=args.model, token_name=args.key)
    data = summarize(logs, args.by)
    total = sum(l.get("quota", 0) for l in logs)
    stat = None
    try:
        stat = api.log_stat(start, end, group=args.group, model=args.model, token_name=args.key)
    except RuntimeError:
        pass
    out = {"since": ts(start), "until": ts(end) if end else "now", "by": args.by, "rows": data,
           "total_quota": total, "total_cny": round(total / QUOTA_PER_CNY, 4), "requests": len(logs),
           "server_stat_quota": (stat or {}).get("quota")}

    def render():
        table([[a["key"], a["requests"], f"{a['cny']:.3f}", a["prompt"], a["completion"], a["cache"], a["cache_write"],
                f"{a['cache_hit']:.0%}" if a["cache_hit"] is not None else ""] for a in data],
              [args.by, "req", "¥", "in", "out", "cache", "cache_w", "hit"])
        print(f"\n{out['since']} → {out['until']}：{len(logs)} 次，{cny(total / QUOTA_PER_CNY, api.env)}")
        if stat and abs(stat.get("quota", 0) - total) > 1:
            print(f"注意：服务端 /api/log/self/stat 合计 {stat['quota']} quota 与逐条求和 {total} 不一致（分页截断或时间边界）")
    emit(args, out, render)


def cmd_snapshot(api: Api, args):
    raw = api.pricing()
    path = Path(args.out or f"packyapi-pricing-snapshot-{dt.date.today().isoformat()}.json")
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    print(f"写入 {path}  catalog_version={raw.get('catalog_version')}  models={len(raw.get('data', []))}")


# ---------- 写 ----------


def _confirm_apply(args, what: str):
    if not args.apply:
        print(f"[dry-run] {what}\n加 --apply 才真正执行。")
        return False
    return True


def cmd_key(api: Api, args):
    sub = args.key_cmd
    if sub == "create":
        body = {"name": args.name, "group": args.group, "expired_time": -1 if not args.expires else int(time.time() + args.expires * 86400),
                "unlimited_quota": args.quota_cny is None, "remain_quota": int((args.quota_cny or 0) * QUOTA_PER_CNY),
                "model_limits_enabled": False, "model_limits": "", "allow_ips": args.allow_ips or ""}
        if not _confirm_apply(args, f"POST /api/token/ {json.dumps(body, ensure_ascii=False)}"):
            return
        d = api.request("POST", api.admin + "/api/token/", body)["data"]
        key = d.get("key")
        print(f"已创建 key id={d.get('id')} name={args.name} group={args.group}")
        print("密钥只在此刻返回一次，请立刻保存到 .env：")
        print(f"PACKY_KEY_{args.group.upper().replace('-', '_')}={key}")
        return
    # 其余子命令需要定位 token
    toks = api.tokens()
    target = None
    for t in toks:
        if (args.id and t["id"] == args.id) or (args.name and t["name"] == args.name):
            target = t
            break
    if not target:
        die("找不到该 key（用 `packyctl keys` 看 id / name）")
    tid = target["id"]
    if sub in ("enable", "disable"):
        status = 1 if sub == "enable" else 2
        if not _confirm_apply(args, f"PUT /api/token/?status_only=true id={tid} status={status}"):
            return
        api.request("PUT", api.admin + "/api/token/?status_only=true", {"id": tid, "status": status})
        print(f"{target['name']} → {'启用' if status == 1 else '停用'}")
    elif sub == "update":
        full = api.token(tid)
        if args.new_name:
            full["name"] = args.new_name
        if args.quota_cny is not None:
            full["unlimited_quota"] = False
            full["remain_quota"] = int(args.quota_cny * QUOTA_PER_CNY)
        if args.unlimited:
            full["unlimited_quota"] = True
        if args.expires is not None:
            full["expired_time"] = -1 if args.expires == 0 else int(time.time() + args.expires * 86400)
        if not _confirm_apply(args, f"PUT /api/token/ {json.dumps({k: full[k] for k in ('id', 'name', 'group', 'unlimited_quota', 'remain_quota', 'expired_time')})}"):
            return
        api.request("PUT", api.admin + "/api/token/", full)
        print(f"已更新 {full['name']}")
    elif sub == "delete":
        if not _confirm_apply(args, f"DELETE /api/token/{tid} ({target['name']}, group={target.get('group')})"):
            return
        api.request("DELETE", api.admin + f"/api/token/{tid}")
        print(f"已删除 {target['name']} (id={tid})")


def build_request(endpoint: str, model: str, prompt: str) -> tuple[str, dict]:
    if endpoint == "anthropic":
        return "/v1/messages", {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": prompt}]}
    if endpoint == "openai":
        return "/v1/chat/completions", {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 8}
    if endpoint == "openai-response":
        return "/v1/responses", {"model": model, "input": [{"role": "user", "content": prompt}], "max_output_tokens": 16}
    raise ValueError(endpoint)


def usage_from_response(endpoint: str, body: dict) -> dict:
    u = body.get("usage") or {}
    if endpoint == "anthropic":
        return {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
                "cache_tokens": u.get("cache_read_input_tokens", 0), "cache_creation_tokens": u.get("cache_creation_input_tokens", 0)}
    if endpoint == "openai":
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        return {"prompt_tokens": u.get("prompt_tokens", 0) - cached, "completion_tokens": u.get("completion_tokens", 0),
                "cache_tokens": cached, "cache_creation_tokens": 0}
    cached = (u.get("input_tokens_details") or {}).get("cached_tokens", 0)
    return {"prompt_tokens": u.get("input_tokens", 0) - cached, "completion_tokens": u.get("output_tokens", 0),
            "cache_tokens": cached, "cache_creation_tokens": 0}


def cmd_probe(api: Api, args):
    pr = Pricing(api.pricing())
    if args.model not in pr.models:
        die(f"价格表没有 {args.model}")
    if args.group == "cc":
        die("cc 分组只放行 Claude Code 官方客户端，禁止代理/探针调用，会触发反滥用")
    key = args.key or group_keys(api.env).get(args.group)
    if not key:
        die(f"没有分组 {args.group} 的 key（.env 里 PACKY_KEY_{args.group.upper().replace('-', '_')}，或 --key）")
    allowed = pr.endpoints(args.group, args.model)
    endpoint = args.endpoint or next((e for e in ENDPOINT_PREF if e in allowed), None)
    if not endpoint:
        die(f"{args.group}/{args.model} 没有可用协议：{allowed}")
    r = pr.ratios(args.group, args.model)
    est = quota_for({"prompt_tokens": 20, "completion_tokens": 8}, r)
    # 内容必须随机：重复相同请求体会被上游判为"监控探针重复模式"封 key×模型
    prompt = f"Reply with the single word OK. (nonce {random.randint(10**8, 10**9)} {int(time.time())})"
    path, body = build_request(endpoint, args.model, prompt)
    if not _confirm_apply(args, f"POST {api.base}{path} model={args.model} group={args.group} 协议={endpoint}，预计扣 ≈{est:.0f} quota（¥{est / QUOTA_PER_CNY:.5f}）"):
        return
    headers = {"User-Agent": args.user_agent or UA}
    if endpoint == "anthropic":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {key}"
    start = int(time.time())
    rec = {"group": args.group, "model": args.model, "endpoint": endpoint, "ok": False, "start": start}
    t0 = time.time()
    try:
        resp = api.request("POST", api.base + path, body, headers=headers, auth=False, timeout=120)
        rec["ok"], rec["usage"] = True, usage_from_response(endpoint, resp)
    except RuntimeError as e:
        msg = str(e)
        if endpoint == "openai" and "max_completion_tokens" in msg:
            body["max_completion_tokens"] = body.pop("max_tokens")
            try:
                resp = api.request("POST", api.base + path, body, headers=headers, auth=False, timeout=120)
                rec["ok"], rec["usage"] = True, usage_from_response(endpoint, resp)
            except RuntimeError as e2:
                rec["error"] = str(e2)
        else:
            rec["error"] = msg
    rec["latency_s"] = round(time.time() - t0, 2)
    if rec["ok"]:
        rec["expected_quota"] = round(quota_for(rec["usage"], r), 2)
        print(f"响应 OK {rec['latency_s']}s usage={rec['usage']} 价格表预期扣 {rec['expected_quota']} quota；等 {args.wait}s 读扣费日志…")
        time.sleep(args.wait)
        logs = api.logs(start - 5, model=args.model)
        logs = [l for l in logs if l.get("group") == args.group]
        if logs:
            lg = max(logs, key=lambda l: l["created_at"])
            rec["log"] = {"quota": lg["quota"], "usage": log_usage(lg), "ratios": log_ratios(lg), "token_name": lg.get("token_name")}
            rec["log_matches_pricing"] = abs(quota_for(log_usage(lg), r) - lg["quota"]) <= max(1.0, 0.02 * lg["quota"])
            rec["input_inflated"] = log_usage(lg)["prompt_tokens"] > 1000
        else:
            rec["log"] = None
    emit(args, rec, lambda: print(json.dumps(rec, ensure_ascii=False, indent=2)))


# ---------- main ----------


def main() -> int:
    ap = argparse.ArgumentParser(prog="packyctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="JSON 输出（给 agent / 脚本用）")
    sp = ap.add_subparsers(dest="cmd", required=True)

    sp.add_parser("balance", help="账户余额（¥，括号 ≈$）").set_defaults(fn=cmd_balance)
    sp.add_parser("groups", help="分组倍率、说明、本地是否有 key").set_defaults(fn=cmd_groups)
    p = sp.add_parser("models", help="价格表模型列表")
    p.add_argument("--group", "-g")
    p.add_argument("--endpoint", "-e", choices=["anthropic", "openai", "openai-response", "gemini"])
    p.add_argument("--search", "-s")
    p.add_argument("--all", action="store_true", help="包含按次计费（图片/视频）模型")
    p.set_defaults(fn=cmd_models)
    p = sp.add_parser("price", help="某模型在各分组的买价（¥/M 与 $/M）")
    p.add_argument("model")
    p.add_argument("--group", "-g")
    p.set_defaults(fn=cmd_price)
    p = sp.add_parser("cheapest", help="某模型最便宜的分组排序（默认只看有 key 的，排除 cc）")
    p.add_argument("model")
    p.add_argument("--include-no-key", action="store_true")
    p.add_argument("--include-cc", action="store_true")
    p.add_argument("--weights", nargs=2, type=float, default=[1.0, 1.0], metavar=("IN", "OUT"), help="打分权重，默认 1 1")
    p.set_defaults(fn=cmd_cheapest)
    sp.add_parser("peak", help="高峰加价规则与当前生效状态").set_defaults(fn=cmd_peak)
    sp.add_parser("keys", help="账户下全部 API key（不显示明文）").set_defaults(fn=cmd_keys)
    p = sp.add_parser("logs", help="逐条扣费日志")
    p.add_argument("--since", default="24h", help="24h / 7d / 2026-01-31 / unix")
    p.add_argument("--until")
    p.add_argument("--group", "-g")
    p.add_argument("--model", "-m")
    p.add_argument("--key", "-k", help="token_name")
    p.add_argument("--limit", "-n", type=int, default=200)
    p.add_argument("--verify", action="store_true", help="用日志里的倍率复算 quota，标出不符")
    p.set_defaults(fn=cmd_logs)
    p = sp.add_parser("spend", help="消费汇总")
    p.add_argument("--since", default="7d")
    p.add_argument("--until")
    p.add_argument("--by", choices=["group", "model", "key", "day", "hour"], default="group")
    p.add_argument("--group", "-g")
    p.add_argument("--model", "-m")
    p.add_argument("--key", "-k")
    p.set_defaults(fn=cmd_spend)
    p = sp.add_parser("snapshot", help="保存价格表快照 JSON")
    p.add_argument("--out", "-o")
    p.set_defaults(fn=cmd_snapshot)

    p = sp.add_parser("key", help="管理 API key（默认 dry-run，--apply 才执行）")
    ks = p.add_subparsers(dest="key_cmd", required=True)
    c = ks.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--group", required=True)
    c.add_argument("--quota-cny", type=float, help="额度上限（¥），不填 = 无限额度")
    c.add_argument("--expires", type=float, help="多少天后过期，不填 = 永不")
    c.add_argument("--allow-ips")
    for name in ("enable", "disable", "delete", "update"):
        u = ks.add_parser(name)
        u.add_argument("--id", type=int)
        u.add_argument("--name")
        if name == "update":
            u.add_argument("--new-name")
            u.add_argument("--quota-cny", type=float)
            u.add_argument("--unlimited", action="store_true")
            u.add_argument("--expires", type=float, help="天数；0 = 永不过期")
    for sub in ks.choices.values():
        sub.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_key)

    p = sp.add_parser("probe", help="发一条最小请求并核对真实扣费（会花钱，需 --apply）")
    p.add_argument("model")
    p.add_argument("--group", "-g", required=True)
    p.add_argument("--endpoint", "-e", choices=["anthropic", "openai", "openai-response"])
    p.add_argument("--key", help="覆盖 .env 的分组 key")
    p.add_argument("--wait", type=int, default=15, help="等日志落库秒数")
    p.add_argument("--user-agent")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_probe)

    args = ap.parse_args()
    env = load_env()
    api = Api(env)
    try:
        args.fn(api, args)
    except RuntimeError as e:
        die(str(e), 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
