# 真 so 根因研究（零注册）— 2026-07-12

## 问题
协议注册 challenge 要求 so，但 `has_so=false` / `so_len=0`。
Step A/B 无 so 号均在 ~7–8min `token_revoked`；Jennifer 真 so≈2914 活 ≥213min。

## 离线实验（不耗号）

路径：`capture/so-research-20260712/`

1. 经 lajiao 代理 `POST sentinel/req flow=oauth_create_account`
2. 将 challenge 喂给 `vendor/sentinel/sentinel-runner.js` + `sdk.js`
3. 解析 stdout

### Challenge（服务端）
| 字段 | 值 |
|------|-----|
| keys | expire_after, expire_at, persona, proofofwork, so, token, turnstile |
| so.required | **true** |
| so.collector_dx | str len≈17352 |
| so.snapshot_dx | str len≈18628 |
| turnstile / pow | 均 required |

### Runner 输出（Node VM）
| 字段 | 值 |
|------|-----|
| keys | **`p, t, c, id, flow` — 无 `so`** |
| p | gAAAAA… len≈597 |
| **t** | base64 解码 = **`0: SyntaxError: Expected ',' or ']' after array element in JSON at position 48`** len=132 |
| c | gAAAAA… len≈2168 |
| HAS_SO | **False** |
| returncode | 0（成功退出，但 t 已是假值） |

### Jennifer 真值对照（create_account 头）
| 字段 | Jennifer | 协议 runner |
|------|----------|-------------|
| openai-sentinel-token keys | p,t,c,id,flow | p,t,c,id,flow |
| t | 二进制样，len≈**1340** | **SyntaxError 明文**，len=132 |
| openai-sentinel-so-token | **有** `{so,c,id,flow}` wrapper≈2914，so≈612 | **无** |
| so 在 token JSON 内？ | 否（独立头） | 否 |

## 代码路径结论

1. **Runner 只调** `SentinelSDK.token(flow)`（`sentinel-runner.js:721`），无独立 so API。
2. **so 若出现**，Python 从 token JSON 的 `so` 字段组 `openai-sentinel-so-token`（`build_so_header`）——形状与 Jennifer 一致。
3. **当前缺 so 的原因不是 Python 丢弃**：stdout **根本没有 so 键**；连 turnstile 的 `t` 都是 SyntaxError 假值。
4. 若将来 runner 产出 `so` 且含 `SyntaxError` / `MDogU3ludGF4`，`auth.make_sentinel_headers` 会丢弃——这是正确防护，不能关掉去“凑 has_so”。
5. 参考 `699.chat-GPT协议注册` 同一策略：有 so 才带头，否则 None，不强制。

## 根因排序（证据级）

| 优先级 | 假设 | 证据 | 状态 |
|--------|------|------|------|
| **P0** | Node VM 沙箱跑 sdk 无法完成真实 turnstile/so 的 dx 执行 → `t` 崩成 SyntaxError，`so` 不产出 | 离线复现 t 明文 SyntaxError；无 so 键 | **已证实** |
| P1 | 真 so 依赖浏览器行为采集（session observer / 真实 iframe DOM 事件），VM mock DOM 不够 | challenge 给了 collector_dx/snapshot_dx，runner 仍无 so | 强怀疑 |
| P2 | sdk.js 版本/build 与 auth 页不一致导致部分路径失败 | 可能，次于 P0 | 未证 |
| 排除 | Python 未请求 so / 故意 require_so=False 导致不生成 | challenge 已 required；生成在 runner，require_so 只影响是否硬失败 | **排除** |
| 排除 | build_so_header 形状错 | 与 Jennifer `{so,c,id,flow}` 一致 | **排除** |

## 明确不能做
- 把 SyntaxError 的 t/so 当真值发出
- 手写假 so 字符串塞进 header
- 为“凑 has_so”关掉假 so 丢弃逻辑
- 同根邮箱继续盲注册验证 so（已证明无 so 必短死）

## 可行后续（按成本）

### A. 继续零耗号（推荐下一步）
1. 在 runner 加 debug：token 前后 dump keys；turnstile dx 执行是否 throw（已有 `--debug-dx`）
2. 对照 699 / k12 runner 是否同一局限（预期：同一 VM 路线都产不出真 so）
3. 评估 **浏览器辅助只产 sentinel**（Playwright 真页跑 sdk → 导出 token+so，协议只负责 OTP/create）——单变量、不伪造

### B. 有新根邮箱后再测
仅当 A 能稳定拿到 **非 SyntaxError 的 t + 真 so** 时：
单号 create 带头 so + 现有 post_login → 15/30/40/120 retest

### C. 不优先
完整 chat-requirements finalize（同样要真 pow/turnstile，VM 同类失败）

## 一句话
> **缺 so 不是“没带头发”，是 Node VM 里的 SDK 根本产不出真 so；连 `t` 都是 SyntaxError 假 turnstile。**  
> 与 Jennifer 差距在 **浏览器真 SDK 执行环境**，不在 post-login 最小集。
