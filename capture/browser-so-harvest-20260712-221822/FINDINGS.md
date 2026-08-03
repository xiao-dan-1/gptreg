# P0 浏览器产 so — 已打通（2026-07-12）

## 关键修正（根因级）

| 旧假设 | 事实 |
|--------|------|
| `SentinelSDK.token(flow)` 应返回带 `so` 的 JSON | **否**。Jennifer 的 token 也是 `{p,t,c,id,flow}`，**无 so 字段** |
| 缺 so = Node VM / 缺真页 | 真页调 `token()` 同样无 so 字段 |
| so 从 token JSON 的 `so` 键组装 | **错位**。真 so 来自 **`SentinelSDK.sessionObserverToken(flow)`** |

Jennifer create_account：
- `openai-sentinel-token` = `{p,t,c,id,flow}`（t≈1340，**无 so 键**）
- `openai-sentinel-so-token` = `{so,c,id,flow}`（wrapper≈2914，so≈612）

本地 `vendor/sentinel/sdk.js` 导出：
- `init` / `token` / **`sessionObserverToken`** / `timing`

`sessionObserverToken` 逻辑摘要：取缓存 challenge 的 `snapshot_dx` 执行 → 得到 so，再 `ve({so, c: challenge.token}, flow)`。

## 本轮实验

脚本：`capture/browser_so_harvest.py`  
产物：本目录 `harvest.json` / `HARVEST.md`

| 条件 | 值 |
|------|-----|
| Chrome | Playwright `channel=chrome` headless |
| proxy | `127.0.0.1:7890`（chain_via） |
| page | `https://auth.openai.com/about-you` |
| flow | `oauth_create_account` |
| API | `token()` + 交互等待 + `sessionObserverToken()` |

| # | has_so | so_len | so_header_len | t_len | t_syntax |
|---|--------|--------|---------------|-------|----------|
| 1 | **true** | 480 | 2654 | 1316 | false |
| 2 | **true** | 464 | 2702 | 1300 | false |

量级：与 Jennifer 同形状；so 字段略短（~470 vs ~612）、wrapper ~2.6k vs ~2.9k，属可接受差异（headless/未登录态行为较少）。

## 对照链

```
pow 路径:     t=""        has_so=false
Node file:    t=SyntaxError has_so=false
Node url:     t 真形态     has_so=false  （runner 只调 token()）
真页 token(): t 真(~1300)  token 内无 so
真页 + sessionObserverToken: t 真 + has_so=true  ← P0 打通
```

## 对协议代码的含义

1. `build_so_header(token_json)` **只从 token JSON 取 so 永远失败**（与 Jennifer 一致：so 不在 token 里）。
2. Node runner 只调 `token()` → 永不出 so，不是「再 mock 一点 DOM」能修。
3. 正确接缝：浏览器 helper 返回 `(token_json, so_header)` 两路；`create_account` 分别带头。
4. 假 so 过滤仍保留。

## 下一步（P0.5 / P1）

**P0.5 协议 opt-in（小改，不改默认 pow）**
- `make_sentinel_headers` 或 pipeline 支持注入外部 `token` + `so_header`
- 或 `protocol.sentinel_source: pow|browser`
- 默认仍 pow；browser 仅研究/显式开关

**P1 存活（需新根邮箱）**
- A：browser so + 现 post_login → retest 15/30/40/120
- B：pow 无 so 对照
- 禁止同根 JohnOwens 盲刷

## 禁止

- 伪造 so / 关假 so 过滤
- 以为改 PoW 能出 so
- 无 P1 证据宣称 delayed ban 已解
