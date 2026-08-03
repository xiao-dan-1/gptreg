# Protocol checklist from Jennifer (session-retry-20260711-211251)

正样本：浏览器注册，create so 真值，延迟健康 ≥185min 仍 ok（2026-07-12 00:22 复测）。
用途：改协议时逐项对照。**禁止伪造 so。**

## create_account POST auth.openai.com/api/accounts/create_account

| 项 | Jennifer 浏览器 | 协议现状 | 动作 |
|----|-----------------|----------|------|
| body `name`+`birthdate` | 有 | 有 | 保持 |
| referer about-you | 有 | 有 | 保持 |
| `openai-sentinel-token` `{p,t,c,id,flow}` | 有 | 有 | 保持 |
| `openai-sentinel-so-token` `{so,c,id,flow}` | **真 so，wrapper len≈2914，so 字段 len≈612** | 可选；假 so 丢弃；常无 | **Step A 观测**；无真 so 不造假 |
| Authorization | 无 | 无 | 保持 |
| oai-device-id | **无** | auth_api_headers 常带 | 低优先；勿当第一刀 |
| origin | 抓包未见 | 常带 | 低优先 |
| UA | Chrome/150 | config Chrome/142 | 低优先 |
| accept-language | 抓包未见 | 常带 | 低优先 |
| datadog/trace 头 | 有 | 无 | 忽略（遥测噪声） |

## email-otp/validate

| 项 | Jennifer | 协议 | 动作 |
|----|----------|------|------|
| body `{"code"}` | 有 | 有 | 保持 |
| sentinel-token / so | **无** | 可带 | **非存活关键**；勿过度加码 |

## post-login（callback 后，协议当前基本无）

浏览器有、协议无的**最小候选**（先这 3 个，勿一次复刻 30 个 onboarding）：

1. `POST /backend-api/sentinel/chat-requirements/prepare`
2. `POST /backend-api/conversation/init`
3. `POST /backend-api/sentinel/chat-requirements/finalize`

其余 settings/onboarding/models 等先记为噪声，Step B 再考虑。

## Step A 观测字段（不改行为）

注册一次后日志/结果必须能回答：

- `challenge.so.required` / `need_so`
- `has_so` / `so_len`（header 级）
- 是否丢弃假 so（SyntaxError）
- create_account 最终 `has_so`
- t0 health + 15/30/40/120 retest

## 实验顺序

1. Jennifer 长测（进行中 → 6h）
2. Step A：协议单号，**只观测 so**，不造假
3. Step B：so 策略不变，只加 post-login 最小 3 接口
4. 仍死 → 邮箱根 / 代理隔离

## 号池门槛

Step A 需要可用邮箱。

## Step A 实测 — JohnOwens2952（2026-07-12 00:30）

| 字段 | 值 |
|------|-----|
| reg email | `JohnOwens2952+ae8294@outlook.com` |
| t0 health | ok 200 / me 200 |
| need_so | **true** |
| has_so | **false** |
| so_len | **0** |
| challenge_so_keys | collector_dx, required, snapshot_dx |
| post_login | false（未改） |
| proxy | lajiao US sid=kCEt8qfp via 7890 |
| runner | challenge 要 so；Node 未产出 so；继续且不带 so-token（未造假） |

### 延迟复测（JohnOwens）

| checked_at | age_min | health | raw code |
|------------|---------|--------|----------|
| 00:30:44 | 0.4 | ok 200 | — |
| 00:30:57 | 0.7 | ok 200 | — |
| 00:32:16 | 2.0 | ok 200 | — |
| **00:37:17** | **7.0** | **invalidated 401** | **`token_revoked`** |
| 00:42:18 | 12.0 | dead | token_revoked |
| 00:47:19 | 17.0 | dead | token_revoked |

endpoint: `backend-api/accounts/check` + `/me`  
message: `Encountered invalidated oauth token for user, failing request`  
code: **`token_revoked`**（与部分历史号的 `token_invalidated` 同类 401 废 token）

结论：协议侧 **challenge 要求 so，但 runner 给不出真 so**。与 Jennifer（真 so wrapper≈2914，≥193min 仍活）差距坐实；本号在 **2–7 min 窗内**后置废 token。  
下一步：Step B（post-login 最小集，so 策略仍不变）或真 so 生成路径研究——仍禁止假 so。

## Step B 实现 + 实测 — JohnOwens+41dcda（2026-07-12 00:55）

### 代码（单变量：只加 post-login）
- `auth.post_login_warmup`：`/me` → `conversation/init` → `chat-requirements/prepare`
- **不** POST finalize（无真 pow/turnstile，禁止伪造）
- create 仍 `require_so=False` / 假 so 丢弃
- `config.yaml register.post_login: true`

### 注册结果
| 字段 | 值 |
|------|-----|
| reg email | `JohnOwens2952+41dcda@outlook.com` |
| t0 health | ok 200 |
| need_so / has_so / so_len | true / **false** / **0** |
| post_login | **true** |
| me | **200** |
| conversation/init | **200** |
| prepare | **200** has_prepare_token=true；pow/turnstile/so required=true |
| finalize | skipped_no_real_pow_turnstile |

对照 Step A（+ae8294，无 post_login）：2–7min `token_revoked`。

### 延迟复测（Step B +41dcda）— DONE 2026-07-12 01:03

| checked_at | age_min | health | raw |
|------------|---------|--------|-----|
| 00:56:07–00:58:37 | 0.4–3.0 | ok 200 | — |
| **01:03:38** | **8.0** | **401** | **`token_revoked`** |

endpoint: `backend-api/accounts/check` + `/me`  
code: **`token_revoked`**（与 Step A 同类）

### Step B 结论（可下）
- 最小 post-login（me + init + prepare，**无 finalize、无 so**）**不能**拉开死亡窗  
- Step A ~7min 死 / Step B ~8min 死 → **同量级**  
- 与 Jennifer（真 so + 浏览器完整 post-login，≥213min 仍活）对照后，优先级：  
  1. **create_account 真 so**（challenge 已 required，Node runner 给不出）  
  2. 完整 chat-requirements finalize（需真 pow/turnstile，成本高、次优先）  
  3. 邮箱根隔离（JohnOwens 同根已烧 2 个别名，**勿再同根刷**）  
- 仍禁止假 so / 假 finalize

### 下一步（不要再盲注册）
1. **零耗号**：继续 Jennifer 6h 长测；把 Step A/B 对照表写入 DIFF  
2. **真 so 研究**（优先，先不耗号）：对照 `vendor/sentinel/sentinel-runner.js` + Jennifer 抓包 so 形状，查为何 `has_so=False`（challenge 有 collector_dx/snapshot_dx）  
3. 有新**不同根**邮箱后再做：单号 + 真 so（若 runner 能产）+ 现有 post_login，15/30/40 复测  
4. 明确不做：假 so、同根 JohnOwens 再刷、先大改 UA/指纹

## 真 so 研究 — DONE 离线 2026-07-12

详见 `capture/so-research-20260712/FINDINGS.md`。

| 项 | 结果 |
|----|------|
| challenge so.required | true；collector_dx/snapshot_dx 均有 |
| runner stdout keys | p,t,c,id,flow — **无 so** |
| t 解码 | **`0: SyntaxError: Expected ',' or ']'...`**（假 turnstile） |
| Jennifer t | 二进制样 len≈1340；so 独立头 wrapper≈2914 |
| 根因 | Node VM + `SentinelSDK.token(flow)` **产不出真 so**；file 模式 t 还是 SyntaxError |
| k12 闭环 | **已对齐**（`sentinel_challenge_mode=url` + 本地 1789 中转） |
| 闭环效果 | **t 假→真**（132 SyntaxError → 804 二进制样）；**so 仍无** |
| 下一步 | 新根邮箱单号测 url 模式延迟存活；so 仍无则浏览器辅助；**仍禁止假 so** |
