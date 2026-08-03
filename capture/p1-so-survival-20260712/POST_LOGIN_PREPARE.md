# post_login prepare 深挖（选项 2，2026-07-12）

零耗号：读 Jennifer 抓包 + 复用 A/B 账号再打 prepare；**不 finalize、不造 so**。

## 1. 两套 sentinel，别混

| 维度 | 注册 create | 登录后 chat |
|------|-------------|-------------|
| 主机 | `sentinel.openai.com` | `chatgpt.com` |
| 路径 | `/backend-api/sentinel/req` | `/backend-api/sentinel/chat-requirements/prepare` → **finalize** |
| 入参 | `{p, id=device, flow}` | prepare: `{p}`；finalize: 需解后的 proof（抓包 body 被剥空） |
| 身份 | 匿名/注册会话 | `Authorization: Bearer` + `chatgpt-account-id` + `oai-device-id` + `oai-session-id` |
| persona | `chatgpt-noauth`（live /req） | `chatgpt-freeaccount`（A/B prepare） |
| 产出标记 | `token`(=c) | **`prepare_token`**（不是 create 的 c） |
| so | `so.{required,collector_dx,snapshot_dx}` | 注册时日志：`so_required=true`（结构同族，见下） |
| 真 so 产线 | 页内 `sessionObserverToken` + **snapshot_dx** | 浏览器会 load `sentinel/sdk.js` + `frame.html` + 可能再 `/req`，再 finalize |

→ **create 的 openai-sentinel-so-token ≠ chat prepare 自动过关。**  
create 交了真 so，prepare 仍可 `so_required=true` 且我们仍 skip finalize。

## 2. 本仓库 post_login 实际做了什么

`auth.post_login_warmup`（`register.post_login=true`）：

```text
GET  /backend-api/me                         ✅
POST /backend-api/conversation/init          ✅
POST .../chat-requirements/prepare  {p}      ✅ 只观测
POST .../chat-requirements/finalize          ❌ 故意跳过
```

原因写死：无真 pow / turnstile / so 解，**禁止伪造 finalize**。

## 3. A/B 注册时 prepare 观测（已落盘）

| | A Brandon browser create so | B Eric pow 无 so |
|--|----------------------------|------------------|
| me / init | 200 | 200 |
| prepare | 200 | 200 |
| has_prepare_token | true | true |
| pow / turnstile / **so required** | **全 true** | **全 true** |
| finalize | skipped | skipped |
| ~40min+ retest | 仍 ok | 仍 ok |

→ **create 有无 so 不改变 prepare 的 required 三件套。**  
两边都欠 finalize，却都过了历史 7–8min 死窗（至少本样本）。

## 4. Live 复打 prepare（选项 2 当次）

- 栈：`BrowserSession` + 现成 access_token（不新注册）
- Brandon：**me/init/prepare 再 200**；`so_required=true`；finalize 仍 skip  
- Eric：本地 chain 隧道口 `127.0.0.1:63005` 偶发连不上（代理抖动，非账号死）
- 产物：`research_pack/post_login_prepare_live.json`

（裸 `curl_cffi.Session` 直连曾 SSL/隧道失败；用注册同款 `BrowserSession` 正常。）

## 5. Jennifer 对照

路径顺序（`DIFF_SUMMARY` / events）：

```text
prepare → conversation/init → finalize（均 200）
并加载: sentinel/frame.html, sdk.js, 以及 chatgpt 域 /backend-api/sentinel/req
```

抓包限制：events 里 prepare/finalize 的 **`post_data` / `body` 为 null**（未存 body），  
只能确认 **打了 finalize 且 200**，看不到 body 字段级形状。

## 6. 历史 Step B 结论（勿忘）

JohnOwens 同根：

| | post_login | create so | 死亡 |
|--|------------|-----------|------|
| Step A | 无 | 无 | ~7min `token_revoked` |
| Step B | me+init+prepare **无 finalize** | 无 | ~8min 同量级 |

→ **半截 post_login 不能当银弹**；优先级仍是 create 真 so，finalize 整链次之。

## 7. 和 create so 体系是否同一套？

| 点 | 判断 |
|----|------|
| VM / dx 形态 | **同族**（pow + turnstile.dx + so collector/snapshot） |
| API 门面 | **不同**（prepare_token vs c；host/path/auth 不同） |
| 会话缓存 | create 用 auth 页 SentinelSDK flow 状态；chat 用 **已登录** chatgpt 页 SDK |
| 交 so 一次是否覆盖 chat | **否**（A 有 create so，prepare 仍 so_required） |

## 8. 代码小改（观测 only）

`post_login_warmup._prepare` 现多记：

- `so_keys` / `so_collector_dx_len` / `so_snapshot_dx_len`
- `turnstile_dx_len` / `pow_difficulty`
- `chatreq` = `summarize_chatreq(...)`（与 /req 探针同形）
- 日志一行带齐 so_required + dx 长度  
- **仍不 finalize**

## 9. 假设排序（更新）

| 优先级 | 假设 | 证据 |
|--------|------|------|
| 高 | create 真 so 是长活主因 | Jennifer 有 so 长活；历史无 so 快死；P1 还在跑 |
| 中 | 完整 prepare+**真**finalize 有加成 | Jennifer 有 finalize 200；半截 Step B 无效 |
| 中低 | 仅 prepare 观测足够 | 当前 A/B 欠 finalize 仍 >40min 活 |
| 低 | post_login 其它 onboarding 噪声 | 路径多，未单变量 |

## 10. 禁止 / 下一步

禁止：假 finalize、假 so、因 prepare.so_required 改默认 pow、盲烧号做 finalize。

可选下一步：
- 等 P1 60/120：若 B 先死而 A 活 → create so 主因坐实，finalize 仍次优先  
- 若双活很长 → 再开「browser 辅助 chat finalize」研究（另开开关，默认关）  
- headed harvest / 外网对照仍可并行
