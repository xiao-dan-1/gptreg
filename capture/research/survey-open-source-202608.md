# 开源社区纯协议注册调研记录（2026-08）

> 目的：研究我们自己的纯协议路线之前，先摸清网上开源项目怎么做纯协议注册、最新进展、关键信息。
> 我们的路线假设：**OTP-only 无密码注册（pow，无 so）→ 登录后 backend-api add_password 补密码 → enroll TOTP**，全程无浏览器。
> 调研日期：2026-08-10

---

## 一、开源项目全景（纯协议实现现状）

> 调研日期 2026-08-10 · 核心结论先行：**so 无纯协议绕过是社区共识；"无密码注册+补密码+TOTP" 有人走通，但补密码走浏览器 Settings 页，不是纯 HTTP 的 backend-api add_password**

### 项目清单

| 项目 | 活跃 | stars | 方式 | 特点 |
|---|---|---|---|---|
| xiaoguzuiniu/gpt-free-register | 2026-07-29 | 192 | **纯协议** | 上游母项目，我们技术线的源头。t=Node vm 跑真 sdk.js；pow=FNV-1a 纯 Python；TOTP=reauth→enroll→activate 全协议 |
| myfanhua/turb-gpt-free-register | 2026-08-08 | 812 | 混合(5 驱动) | 最火 fork；protocol 纯协议 + roxy/cloak/browser_use/skyvern 浏览器 |
| Web3XiaoAn/turb-gpt-free-register | 2026-08-03 | 3 | 混合 | ⭐ **最贴近我们路线**：CREATE_PASSWORD_BY_DEFAULT=False=无密码注册；补密码=浏览器点 Settings 页；补 2FA=mfa/enroll+activate |
| Haohuangsix/gpt-register-pipeline | 2026-07-31 | 36 | 混合 | iCloud Hide My Email 别名做邮箱源（解决号池） |
| klsf/codex-register | 2026-06-27 | 402 | 协议+`--st` | TS；自己解释 turnstile.dx 字节码；`--st` 真浏览器只采 sentinel（复用 context） |
| royp888/codex-register | 2026-04-13 | 29 | 协议+Playwright | "无 Playwright→写操作被拒"（t 缺失）；自动探测 sentinel SDK 版本 |
| Ethan-W20/openai-auto-register | 2026-04-05 | 241 | 纯 API(Go) | 已失效（官方封杀）；500 并发把域名玩黑——提醒并发克制 |
| zbqoumi/oumiFree | 2026-07-15 | 20 | Playwright 采 sentinel + curl_cffi | so 采集 TTL 缓存 600s 复用 |
| leetanshaj/openai-sentinel | 2025-11-13 | 73 | Python 库 | 会话 API token，非注册 flow；PoW 思路同源已过时 |

### Sentinel 三件套的社区真实状态

| 组件 | 社区做法 | 需真浏览器? |
|---|---|---|
| pow | FNV-1a 32 位，seed+base64(config) 前缀匹配 difficulty；纯 Python/TS 秒解 | 否 |
| t(turnstile dx) | ① 自己解释 dx 字节码(klsf) ② 跑真实 sdk.js(Node vm/quickjs)。**纯 Python 造 t 过不了服务端"重跑 sdk.js"深度校验 → OTP 静默丢弃 / 写操作被拒** | 否(必须真 sdk.js) |
| so(sessionObserverToken) | **无任何项目纯协议绕过**。so=字节码 VM 解释 snapshot_dx，读 36 个 `__oai_so_*` 浏览器行为字段，无真浏览器时字段全空 | **是** |

**so 的三条社区应对**：① 真浏览器跑全程（roxy/cloak/browser_use）；② 开一次真浏览器采 sentinel + TTL 缓存复用（klsf `--st` / oumiFree 600s）；③ 协议模式省略 so、赌该 flow 不校验。**没有任何项目能脱离浏览器纯 HTTP 造出有效 so。**

### 对我们的核心判断

1. **so 无解是社区共识，不是我们能力问题** —— 可照抄 klsf sentinel-browser.ts（复用单浏览器 context 只采一次）或 oumiFree 600s 缓存，把 so 采集成本压到最低
2. ⭐ **"无密码注册+补密码+TOTP" 社区走通，且与我们同源**：无密码=OTP 直通（我们 register_otp 已实现）；补 2FA=mfa/enroll+activate（我们已实现）；**但补密码社区唯一实证路径是浏览器点 Settings 页——纯 HTTP 的 backend-api add_password 在公开资料里不存在**（我们要自己证明，属于差异化能力）
3. **t 必须跑真实 sdk.js（quickjs/vm）**，纯 Python 复刻必死 —— 我们的 quickjs 方案是正解
4. **so 校验是 flow/challenge 依赖的**：部分 flow 不校验 so（klsf 协议模式不产 so 也能跑通部分）→ 值得验证我们栈里哪些 flow 强校验 so，强校验分流到浏览器、弱校验保持纯协议
5. **重心转向账号存活**而非注册成功（社区都在做后验证/Codex OAuth/2FA/回收）

---

## 二、turb-gpt-free-register 深挖

> 仓库：github.com/myfanhua/turb-gpt-free-register · 活跃（最近 push 2026-08-08，812 stars）· MIT · 基于 xiaoguzuiniu/gpt-free-register 改造
> 本地副本：`D:\tmp\turbrepo\`

### 核心：so 瓶颈的答案是"跑官方 SDK"，不是逆向

- **sentinel t/so/turnstile/pow 全部由官方 sdk.js 在 Node vm + 浏览器 mock 环境里算出**
- 流程：Python 生成初始指纹 `p`（25 维数组）→ POST `sentinel.openai.com/backend-api/sentinel/req` 拿 challenge → `subprocess` 跑 `node sentinel-runner.js`（`vm.runInContext` 加载官方 sdk.js，mock 全浏览器环境）→ `SentinelSDK.token(flow)` 产出 `{p, t, c, id, flow, so}` → 有 so 就挂 `openai-sentinel-so-token` 头
- **无需真实浏览器、无需指纹浏览器、无需逆向 so 二进制** ← 这就是对我们"so 采集瓶颈"的根本答案
- sdk.js 里有 `sessionObserverToken`，SDK 在 mock 环境里自己能产出 SO

### 注册链路（protocol 驱动，OTP-only）

```
0.5 backend-anon 预热（accounts/check、me、sentinel/chat-requirements/prepare、finalize 等，可失败不打断）
1-3. chatgpt.com/api/auth/providers + csrf + signin/openai
     (login_hint=email&screen_hint=login_or_signup&ext-oai-did=...&ccaps=login_methods)
4.  authorize 重定向：落到 /create-account/password 或 /register → 抛错拒烧邮箱
5.  自动落 /email-verification 触发 OTP 邮件
6.  POST /api/accounts/email-otp/validate {code}  （默认不带 sentinel）
7.  视 page.type：external_url/direct_oauth → 跳过 create_account；否则：
8.  sentinel/req (flow=oauth_create_account) → Node runner 出 token(+so)
9.  POST /api/accounts/create_account {name, birthdate} → continue_url
10. callback/openai 种 session-token → GET /api/auth/session 拿 accessToken
11. authenticated_bootstrap（backend-api 预热）
12. 可选 2FA：reauth → TOTP enroll+activate
```

### 密码：OTP-only 全程无密码，无 add_password

- 协议路径拒绝落 /create-account/password（`openai_auth.py:210` 抛错保邮箱）
- 全仓库搜不到 OpenAI add_password/change_password/eligibility 端点调用
- → **"后端补密码"这条差异化路线在他们实现里没有验证依据，需要我们自己证明端点可用**

### TOTP 激活的关键前置细节（对我们极重要）

- **enroll 前必须先 reauth**：`signin/openai` 带 `reauth=password&max_age=0` → 收新 OTP → `email-otp/validate` → 换一个**新鲜 accessToken**
- 原因：**token 内嵌的 pwd_auth_time 必须是新鲜的，2FA enroll 才接受**
- 然后 `POST /backend-api/accounts/mfa/enroll {factor_type:totp}` → `POST /backend-api/accounts/mfa/user/activate_enrollment {code}`
- ← 与我们记忆的 TOTP 激活链一致，补上了"enroll 前要新 token"这条易漏细节

### 并发

- ThreadPoolExecutor（非 asyncio）；CLI `--workers` 默认 1；WebUI 默认 4、clamp 1..16
- 套餐查询独立池 3 线程、限速 0.4s+jitter；注册线程不阻塞

### 可借鉴点（对我们路线）

1. sentinel 最小化注入：validate 不挂 sentinel，只有 create_account 必挂；so 头仅当 SDK 真产出才加
2. 指纹一致性：oai-did 主动种三域 cookie jar，ext-oai-did/Sentinel id/cookie 同值；出口 IP geo 决定 locale/timezone/UA
3. 资格观察用后台队列（accounts/check → plus_trial_eligible），不占注册线程

### 与自研代码的对照

- 我们的 `_enroll_totp`（register_pwd.py:489）直接用注册会话的 `at` enroll，**无 reauth**。账号刚建时 at 新鲜，enroll 可用（已实测产出 2FA 账号）。
- ⚠️ **新路线注意**：若 OTP 注册 → add_password → enroll 是独立会话、间隔较久，at 可能已不新鲜 → **需先 reauth（signin/openai 带 reauth=password&max_age=0 → 新 OTP → validate）换新鲜 at 再 enroll**。这条要写进新命令设计。

---

## 三、sentinel 绕过与 add_password 路线

### ⭐ 最重要发现：so 不需要真 Chrome（2026 已可脱离）

两条纯程序路线：
1. **Node VM 跑官方 sdk.js 自己算**（gpt-register-pipeline）：`sentinel/req` 响应带 `so.required`，challenge 喂给 `sdk.js`，最终 token JSON **自带 `so` 字段**；Python 端抽出组 `openai-sentinel-so-token` 头
   - `core/openai_auth.py::request_sentinel_token()` + `build_sentinel_header()`；`core/sentinel_runner.py` subprocess 调 Node
2. **纯 HTTP 直接要**（codex_register，791 stars）：同一 `sentinel/req`，`flow=oauth_create_account`，返回的 token 就当 so-token 用（README 声称有效）
   - `codex_register/chatgpt.py::fetch_sentinel_token(flow="oauth_create_account")`

→ **我们 browser_sentinel.py 用真 Chrome 采集 so 是社区最重的做法**。turb-gpt 的 sentinel-runner.js（Node vm + sdk.js）已在仓库内，可直接借鉴替换。

### 2026-07 抓包实证（gpt-register-pipeline/docs）

- `create_account` 请求**同时带** `openai-sentinel-token` 和 `openai-sentinel-so-token`，token 内 `flow=oauth_create_account`
- `email-otp/validate` **没带 sentinel-token**；只有 create_account 才带双 token
- 25 字段 `p` 数组逐项含义、注册→bootstrap 完整链路（`/ces/v1/rgstr`、`chat-requirements/prepare`、`conversation/init`、`finalize`）
- **⚠️ 对"只用 pow 不用 so"的存疑**：从抓包看 create_account 带 so；若实测无 so 能建号成功 → so 可能随 flow/IP/风控非强制；否则用 flow=oauth_create_account 纯 HTTP 补 so

### pow / t 现状

- **pow（新版）**：FNV-1a 32-bit + `base64(config)+"~S"`，前缀 `gAAAAAB`(PoW)/`gAAAAAC`(requirements)，25 字段指纹数组 → 纯 Python 可解（gpt-register-pipeline/core/sentinel.py）
- **pow（旧版）**：SHA3-512 已失效；**纯 Python 旧版表面 200 但 OTP 被静默丢弃**（深度校验）
- **t**：必须跑真实 sdk.js（Node vm/quickjs）；发空 p 拿 token 已不再可用（codex-console 新增 POW 求解，注释"原先直接传空值已经不行了"）
- `/sentinel/req` 表面 200 ≠ 通过深度校验

### add_password：无公开纯 HTTP 实现（差异化机会）

- **没有任何开源项目直接调 `POST /accounts/add_password` 或 `/eligibility`**
- 社区唯一公开做法是**浏览器 UI 自动化**：`roxy_account_security.py::add_password_in_settings` → 登录后打开 `chatgpt.com/#settings/Account` 点 "Add password"
- OpenAI 官方帮助：Google/Microsoft/Apple 登录注册的账号**没有密码可设**；邮箱注册的账号可在 Settings→Account 补设（作用于整个 OpenAI 账号）
- **坑**：TOTP 死锁（自 2023 起）：开了 TOTP 后若验证器失效且无恢复码，所有安全操作都要同一个失效的 TOTP 验证 → 永久锁死
- → **我们的 do_add_password.py 纯协议补密码是社区空白，需要自己逆向前端端点并实测证明**

### TOTP enroll 的前置（交叉验证 x2）

- **enroll 要求 token 内嵌 pwd_auth_time 新鲜** → 必须先重认证换新 accessToken
- **重认证走邮箱 OTP 即可，不需要密码** → 反证：TOTP 段与补密码解耦
- gpt-register-pipeline/core/account_export.py 注释："（此时 token 内嵌的 pwd_auth_time 是新鲜的，2FA enroll 才会接受）"

### 存活（社区口径）

- **注册机账号普遍 3 天内死**（codex_register 作者原话"官方拉闸，基本无法存活超过 3 天"）
- 决定性因素：**IP 纯净度、邮箱域名质量（大量域名被拉黑）、使用行为**（不要短时间耗光额度）——与"有没有密码"无关
- 有作者怀疑 PoW 是钓鱼（"带 PoW 注册的基本活不久"）——推测级，非定论
- **HTTP 401 / token_expired ≠ 封号**：可能只是过期需重登；封号通常有 `account_deactivated` 明确标识

---

## 四、TOTP/2FA 开源实现

### 谁实现了 TOTP 激活（社区仅 2 个）

| 项目 | 链路 | 说明 |
|---|---|---|
| Web3XiaoAn/turb-gpt-free-register | 浏览器(Roxy) | 注册后同 Profile 自动 enroll/activate，有"补设 2FA" |
| **2951461586/GPT-Register-Tool** | **纯协议** | `sms_tool/account_2fa.py`，生产级(SQLite 账号库/测活/恢复/导出) |

codex-register 家族都只到"注册+OAuth"，不激活 TOTP。toSub2 只消费已有 secret 登录。

### TOTP 激活端点链（两项目一致，与我们记忆对齐）

```
1. POST chatgpt.com/api/auth/signin/openai?connection=password&reauth=password&max_age=0
     body callbackUrl=https://chatgpt.com/?action=enable&factor=totp
2. 收 OTP → POST auth.openai.com/api/accounts/email-otp/validate {code} → continue_url
3. 跟 continue_url → GET chatgpt.com/api/auth/session → 重认证后的新 access_token
4. POST /backend-api/accounts/mfa/enroll {factor_type:totp} → {secret, session_id}
5. POST /backend-api/accounts/mfa/user/activate_enrollment {code, factor_type, session_id} → success:true
6. GET /backend-api/models 验证新 token → secret+新 token 落盘
```

- ⚠️ reauth 必须走 `reauth=password` 页面 transaction 提交；直接调 email-otp/validate 在新版重认证会话会返回 401（turb 源码注释）
- **enroll/activate 无 sentinel/so**：头只带 `authorization + oai-device-id + oai-language + content-type` ← 我们的方案成立
- **so 用在哪**：create_account（flow=oauth_create_account）；以及完整新登录的 authorize/continue、OTP send/validate（GPT-Register-Tool `_login_existing_account_with_email_otp`）。**so 是"注册/登录门禁"，不是"2FA 激活门禁"**

### device_id 匹配（对我们路线极关键）

- 两个项目都**沿用注册时的原 device_id**（cookie + header + ext-oai-did 三者一致）
- GPT-Register-Tool `assert_sentinel_device_id()`：sentinel token 的 `id` 字段 == oai-did，不一致重取
- 若原 device_id 丢失 → 社区做法是完整重登录（email OTP 换新 token + 新 device_id）再 enroll
- **服务端是否对 add_password/security_settings 做 device↔token 硬匹配：无确凿证据，属社区空白**——值得我们用不同 device_id 对旧 token 打一次实验验证
- Linux.do 逆向：device_id 可自生成 UUID v4，固定复用即可；服务端不校验真指纹，但要求会话内一致

### 存活率数据（社区）

- 2FA 不是免死金牌：OpenAI 风控已从"验证登录凭证"升级到"验证手机实名/网络信誉"，开 2FA 仍可能被强制手机二验
- Plus 试用号 1 周存活 <26%；giffgaff SIM 绑号封率 <20%
- 注册机泛滥是风控收紧导火索；GV/接码平台号码基本作废

### 对方案启示

- **OTP 注册后补密码/开 2FA 必须沿用原 device_id**，cookie+header+ext-oai-did 一致——两个项目唯一验证过的路径
- 我们 `_enroll_totp` 已沿用注册会话的 device_id，符合社区最佳实践 ✓
- 新路线里 add_password 也应用同一 device_id

---

---

## 五、结论：对我们的路线意味着什么

### 我们假设路线的社区对照评估

| 环节 | 社区现状 | 判定 |
|---|---|---|
| ① OTP-only 无密码注册 | ✅ 社区主流（密码分支已停用） | **可行**（register_otp 已实现） |
| ② 只用 pow 不用 so | ⚠️ 存疑：2026-07 抓包 create_account **带 so** | 需实测验证是否非强制 |
| ③ add_password 补密码 | ❌ **无公开纯 HTTP 实现**，社区走 Settings UI | **差异化空白，需自行逆向实测** |
| ④ enroll TOTP | ✅ 公开实现，重认证走邮箱 OTP **无需密码** | **可行且与补密码解耦** |
| ⑤ 存活 | ❌ 注册机账号普遍 3 天内死（IP/邮箱域名/使用行为主因） | 存活瓶颈不在 token 方案 |

### 三个决定性发现（按杠杆排序）

**1. ⭐ so 不需要真 Chrome —— 最大提速杠杆**
- 2026 主流：Node VM 跑官方 sdk.js 自己算 so（turb-gpt/gpt-register-pipeline），或纯 HTTP `sentinel/req` flow=oauth_create_account 直接要（codex_register）
- 我们的 browser_sentinel.py（真 Chrome 采集）是**社区最重**的做法 → 卡我们的 ~12s/300MB 资源瓶颈可砍掉
- 这条对**主路线（password+TOTP）和新路线都受益**：把 so 采集从"真 Chrome"换成"Node vm 跑 sdk.js"
- 需要验证：mock 环境产出的 so 能否通过服务端校验（turb-gpt 声称可以）

**2. add_password 纯协议是社区空白 —— 差异化机会**
- 无任何开源项目调 `POST /accounts/add_password`；社区只走 Settings UI 自动化
- 若我们的 do_add_password.py 端点实测可用 → 唯一纯协议补密码实现
- 需自行逆向前端端点 + 实测；注意 Google/MS/Apple 登录的账号无密码可设（仅邮箱注册可补）

**3. TOTP 段确认成立，且与密码解耦**
- enroll/activate 无 sentinel，只需 authorization + oai-device-id + oai-language + content-type
- 前置 reauth（换新鲜 pwd_auth_time）走**邮箱 OTP 即可，不需要密码**
- → 新路线可先 OTP 注册 + enroll TOTP（无密码），密码作为可选加分项

### 工程注意点（来自社区实战）

1. **device_id 必须沿用注册原值**（cookie + header + ext-oai-did 一致）；丢失则需完整重登录
2. **reauth 必须走 reauth=password 页面 transaction**，直接 email-otp/validate 会 401
3. t 必须跑真实 sdk.js（quickjs/vm）——我们已是正解，勿回退纯 Python
4. sentinel 最小化：validate 不挂，create_account 才挂
5. 指纹一致性：oai-did 铺三域 + ext-oai-did + sentinel id 同值
6. 401/token_expired ≠ 封号；封号有 account_deactivated 标识
7. 存活主因是 IP 纯净度 + 邮箱域名质量，与密码/2FA 无关

### 建议的验证顺序（补号池后）

1. **验证 so 纯程序获取**：Node vm 跑 sdk.js 出 so → 替代 browser_sentinel（主路线最大提速）
2. **验证无 so 建号**：register_otp 无 so 建号是否成功、存活如何（决定新路线能否纯 pow）
3. **验证 add_password 端点**：do_add_password.py 实测（无 so 是否可补、device 匹配、作用于全账号）
4. **拼装新命令**：OTP 注册 →（可选补密码）→ reauth → enroll TOTP，全纯协议

---

## 六、自研验证进展：so 纯程序获取（2026-08-10）

### vm(quickjs) 已能产出非假 so

`capture/research/probe_vm_so.py` 诊断（6 变体全跑，cloudmail 号池）：

| 变体 | t_len | so_len | so 状态 | oai 字段 |
|---|---|---|---|---|
| default | 988 | 2658 | REAL?(非假) | 36 全 null |
| wait(1.5s) | 1012 | 2706 | REAL? | 36 全 null |
| snap_inject | 984 | 2730 | REAL? | 36 全 null |
| patch | 960 | 2678 | REAL? | 36 全 null |
| inject | 1008 | 2722 | REAL? | 36 全 null |
| extreme | 1028 | 2658 | REAL? | 36 全 null |

- **关键**：所有变体产出的 so 都是干净 base64 blob（`{"so":"..."}` 2600-2730 字符），**无 SyntaxError/MDog 假值**，t 为真 t（960-1030 字符）
- **耗时 1-3.5s，无浏览器** ← 相比 browser_sentinel 的 ~12s/300MB，纯程序 so 是数量级改善
- **行为字段全 null**：vm 无真实浏览器事件，so 编码 null 行为——**服务端是否接受待 create_account 实测**（这也是旧"假 so 必死"实验与当前干净 so 的关键区别：旧假 so 是 TypeError 产物，当前是 SDK 正常产物）
- 注入策略（snap/inject/patch/extreme）未反映到最终 so（dump 仍 null），说明注入机制在当前 sdk.js 版本下可能无效或时机不对

### ✅ create_account 实证通过（2026-08-10 17:36）

`reg_cd0b35@a8f2.xdauv.xyz`（cloudmail）用 **quickjs 引擎（vm t + vm so）** 完整注册成功：

```
sentinel_obs: challenge_mode=quickjs, has_so=True, so_len=2678, t_len=1000
create_attempts: 1 次成功（has_so=True）
post_login: me/conversation_init/chat_requirements_prepare 全 200
finalize: skipped_no_real_pow_turnstile（对齐 P1 策略）
health: ok（accounts/check=200）
total: 30.7s（signin 9.4s / otp 4.3s / create 8.2s / health 8.7s）
```

- **服务端接受 vm so**（create_account 通过、health 200）→ so 纯程序获取完整成立
- **总耗时 30.7s** vs browser 路线 ~50s+（省了 ~12s/300MB Chrome so 采集）
- 多次重复验证：此前 2 次（reg_87fd6e / reg_0412f3）也 create 通过 + health 200，仅因代码 bug 未落盘（已修）

### 附带修复的代码 bug（工作树内，待提交）

1. `register_otp.py`：cloudmail/iCloud 误用 plus 别名 → 收码查主地址永远超时。改为用主邮箱（对齐 batch_totp）
2. `register_otp.py`：467 行 `used_cache.remember(identity, ...)` 引用未定义变量 `identity` → 建号成功后落盘崩溃。改为 `mail_identity_key(account)`

### ⚠️ 存活复测：~26min 被 token_revoked（带混淆，未定论）

```
age=26.4min 独立复测（1024proxy 新 IP）→ 401 invalidated token_revoked
```

**混淆因素（无法归因）**：
1. **IP 换了**：注册用 cliproxy、测活用 1024proxy——OpenAI 可能因新 IP/上下文使 oauth token 失效（安全绑定），不一定是账号死
2. **cloudmail 域名质量**：a8f2.xdauv.xyz 是批量注册域名，可能被风控标黑（研究结论"邮箱域名是存活主因之一"）
3. vm so 质量（行为字段 null）本身也是候选

→ 需要**受控实验**排除混淆：用 1024proxy 注册新 vm-so 账号 + 全程同一 IP 测活（隔离 IP 变量），对比存活。

### ❌ add_password 路线验证：OTP-only 账号不 eligible（2026-08-10 实测）

活账号 `reg_37cb57`（vm-so、OTP-only、1024proxy）实测：

```
GET /backend-api/accounts/add_password/eligibility  → 200 {"eligible":false}
GET /backend-api/accounts/change_password/eligibility → 200 {"eligible":false}
GET /backend-api/accounts/security_settings/info    → 200 {aas_eligible:true, ...}
```

Playwright UI 探测（`probe_add_password.py`）：
- 安全页 `#settings/Security` 只调这 3 个 GET，**main 内容空、无 "Add password" 按钮**
- **UI 与 backend-api 用同一 eligibility 门控** → eligible:false 时 UI 也不提供补密码

**结论**：OTP-only 注册账号无 add_password 资格，"OTP 注册 → add_password 补密码 → TOTP" 路线**不可行**（当前环境/账号状态）。这与社区无纯 HTTP add_password 实现呼应。
**待查疑点**：Web3XiaoAn 声称通过 Settings UI 补密码成功——其账号状态或流程可能不同（如账号成熟/已开 2FA/不同创建方式后 eligibility 变 true）。可后续用"密码创建"或"2FA 后"账号复测 eligibility。

### ⚠️ 受控实验 reg_37cb57：11.8min 也吊销（2/2 全死）

```
t0（0.2min, 1024proxy 不同 sid）→ ok http=200
+10min（11.8min, 1024proxy）       → invalidated http=401 token_revoked
```

- **全程 1024proxy（同一代理商）仍死** → 排除"cliproxy→1024proxy 极端换 IP"单因
- **t0 交叉 IP 检查是 ok 的，+10min 才死** → 不是"换 IP 瞬间 kill"，是服务端**延迟吊销**（~10min 检测窗）
- 2/2 全死（reg_cd0b35@26min、reg_37cb57@11.8min），均 cloudmail a8f2 域 + vm so

**剩余混淆（未归因）**：
1. **cloudmail a8f2 域名被标黑**（批量注册域，研究结论"邮箱域名是存活主因"）
2. **vm so 质量**（行为字段 null → 服务端延迟吊销）
3. **测活本身多 IP**（每次检查换 sid=换 IP，新号多 IP 访问可能触发风控；P1 实验用一致代理路径）

**隔离实验（browser 采集时 so 未产出，实际得到"无 so"对照）：reg_a55964 无 so，7.3min 也死**

| 账号 | so | a8f2 域 | 死亡时间 |
|---|---|---|---|
| reg_cd0b35 | vm so | ✓ | 26min |
| reg_37cb57 | vm so | ✓ | 11.8min |
| reg_a55964 | **无 so** | ✓ | **7.3min** |

**结论：3/3 全灭，无 so 账号死得最快 → 域名（a8f2.xdauv.xyz）是主因，so 类型不是主变量。vm so 不比其他更差。**
**剩余混淆**：测活用旋转 IP（每次新 sid）+ 交叉 IP 检查；P1 实验（outlook、一致代理）存活 2h。若要用 Outlook/高质域名复测 vm so 长活，需 Outlook 池（当前不可用）。

### Outlook 组测受阻：根邮箱全被 OpenAI 标记（非 MS 问题）

测试过程：
1. 22 个"可用"账号中 3 个被 MS 标 service abuse mode（Kaitlyn/Kelly/Nicholas）
2. **剩余 19 个 token 刷新正常**（xdauv 能拉件）——但**每个根邮箱都有 5-10 封 OpenAI 码、最近活动 08-04~08-07**
3. 今天(08-10)注册 `JasonCopeland6778+e92545@outlook.com`，等 200s **无 OTP 到件**（邮箱最后邮件 08-07）

**结论**：19 个"可用"账号的**根邮箱全被 OpenAI 风控记住**（之前批次用别名注册，主号未进 used 状态，但 OpenAI 记住根邮箱的 OTP 活动）。**新注册的 OTP 不会发**。
→ **要测 vm-so 存活，需真正全新的 Outlook 账号**（无任何 OpenAI 历史）。当前池在 OpenAI 层面已耗尽。

**⚠️ 上述"OTP 真不到件"结论是错的——根因在收码客户端 3 个 bug**（OTP 其实到了，被过滤/漏提取）：
1. `icloud_xdauv.py::_fetch`：`filter_recipient: True` 按主号过滤，**别名收件的 OTP 被滤掉**（Outlook 注册用别名）→ 改 False + limit 50
2. `icloud_xdauv.py::wait_for_otp`：用了**未导入的 `extract_otp`**（NameError，filter 修好后循环体才执行到）→ 改用本文件 `_extract_otp_from_text`
3. `register_otp.py`：`otp_after` 在 signin_flow **之后**抓 → OTP 若 <1s 到件，时间戳 < after_ts 被当旧件过滤 → 移到 signin 之前

### ✅ Outlook vm-so 注册打通（2026-08-10 19:40）

```
PaulTorres9077+9baf19@outlook.com  health=ok has_so=True so_len=2706 t_len=1004
OTP 15.7s 到件(xdauv, filter 修复后)  total=43.0s  alias=True
```

- **根邮箱并未被 OpenAI 标记**（之前判断错误）——OTP 正常到达，只是客户端 3 个 bug 挡住
- 19 个"可用" Outlook 账号**全部可用于注册**（3 个 MS 滥用除外）
- **vm-so 存活测试进行中**（+11/+30/+60min 已排）——这是判定"域名 vs vm so"的关键

### ⭐ 决定性发现：多 IP 轮换测活是杀手（2026-08-10 20:23）

**受控 A/B 对照（Outlook + 钉住 IP t-30 粘性）**：

| 账号 | so | 测活方式 | 结果 |
|---|---|---|---|
| PaulTorres | vm so | **多 IP**（每次新 sid） | 死@19.5min |
| **BarbaraNolan** | vm so | **同 IP**（PINSO1） | **活@21.1min+** ✓ |
| ElizabethJames | browser so | **同 IP**（PINSO2） | 活@16.6min+ ✓ |

**结论：多 IP 轮换测活杀账号，vm so 和域名都不是主因。**
- 同账号类型（vm so + Outlook）、唯一变量=测活 IP 一致性
- BarbaraNolan 跨过 PaulTorres 19.5min 死亡点仍活

**重构之前的"死亡"结论**：cloudmail a8f2 的 3 账号、PaulTorres 全用旋转 IP 测活 → 全是被**测活方法**杀死，不是域名/so。a8f2 域名可能并未被标黑。

**实践含义**：
1. **测活必须钉住注册时同 IP**（否则检查本身会杀号）
2. vm so 纯程序获取**可正常存活**（同 IP 下）
3. 主路线 batch_totp 的测活（survival 命令轮换 IP）需改为同 IP 或接受其杀号风险

### ⚠️ 修正：同 IP 只推迟不阻止（31.1min 也死）

BarbaraNolan（vm-so, 同 IP PINSO1）：
```
t0(0.6) 5.1 9.2 13.1 17.1 21.1 → 全 ok，31.1min → invalidated
```
- 同 IP 把死亡从 19.5min(PaulTorres) 推迟到 21~31min，**但没阻止**
- **7 个账号（不同域/so/同多 IP）全在 7-31min 内死**

**新主因假说：1024proxy IP 被 OpenAI 标黑**
- 所有当前账号都用 **1024proxy**（今天刚换）；P1 实验（2h 存活）用 **cliproxy**
- 与调研结论一致："**IP 纯净度是存活主因**"
- cliproxy 虽 auth 不稳，但 IP 质量保住了 2h；1024proxy 虽稳定但 IP 可能已被烧

**待验证**：
- 用第三个代理（或 cliproxy 恢复）同账号类型对照 → 验证 1024proxy IP 论
- 若属实：1024proxy 只适合建号研究，不适合生产存活；需换高质住宅代理

### ⭐ so 类型影响存活实锤（2026-08-10 20:47）

**受控对照（同 Outlook + 同 1024proxy + 同钉 IP，唯一变量 = so 来源）**：
- **vm so**：PaulTorres 死@19.5min / BarbaraNolan 死@21-31min
- **browser so**：ElizabethJames **活@41.7min+**（约为 vm so 存活时长 2 倍）

**结论：vm so（行为字段 null）撑不过 ~30min；browser so（真实行为字段）长活。** 与历史 FINDINGS"真 so 配 vm t 能活"一致。代理论（1024proxy IP）部分被推翻——同代理下 browser so 明显更久。

**研究含义**：
1. **纯协议 vm so 可建号（过 create），但账号 ~30min 内被吊销** → 只能用于"短窗建号"类用法
2. **长活账号仍需 browser 真 so**（so 采集是长活必需，无法被 vm so 替代）
3. vm so 价值：证明协议链路全通（t+so 纯程序），为研究/调试提供 30min 窗口

### ✅ browser-so 长活确认：61.7min（2026-08-10 21:07）

- **browser so**：ElizabethJames **活@61.7min**（对照 vm so 的 2-3 倍）
- vm so 19.5-31min 全灭 vs browser so 61.7min+ → **so 质量是长活决定因素，实锤**

**研究主线结论（最终）**：
1. **vm so 纯程序建号可行**（create 过、health ok）但账号 **~30min 吊销** → 只够"短窗建号/调试"
2. **browser 真 so 是长活硬需求**（61.7min+，趋势指向 P1 的 2h）
3. so 采集（真浏览器）**无法被纯协议替代**——它仍是长活账号的必经之路
4. 纯协议的价值：证明链路全通 + 提供快速建号（1-3s vs 12s so）与 30min 实验窗口

### ⚠️ 社区调研修正（2026-08-10，agent 报告）

**社区存活数据**（linux.do / Gpt-Agreement-Payment 实证）：
- **30min/61.7min 在社区属"异常短"**（主流小时/天/月）→ 强烈指向 **IP/邮箱环境**而非 so 来源
- 反欺诈实证：Probe 层按**精确 IP 字符串**打标（每 IP ~4-5 次注册翻车）+ Ban 层延迟批审；单 IP 密集注册 24h 存活 ~2%
- **401 ≠ 封号**：401 常只是 token 失效，邮箱 OAuth 重登可复活；重登触发二次接码才死
- **无任何社区证据说"so 行为字段 null 缩短存活"**——主导因素排序：IP 纯净度 ≥ 邮箱域名 > 批次关联 > 使用强度 > 注册方式

**结论修正**：
1. "vm so 是长活杀手" **过强**——社区不认可 so 字段影响存活
2. **1024proxy IP 被 OpenAI 打标**是最大嫌疑（所有走它的账号都异常短命，browser so 61.7min 也短）
3. A/B 差异（vm so 30min vs browser so 61.7min）真实但幅度小 = "坏环境下 browser so 稍好"
4. 30min/61.7min 可能是 **token 失效可重登**（非永久封），勿急判死

**可操作**：① 查 1024proxy IP 复用/打标情况 ② 401 先邮箱重登抢救 ③ 换干净住宅代理对照

### IP 打标测试：1024proxy IP 未被即时打标（2026-08-10 22:18）

同 IP（IPTEST1）连注册 6 个 → **5/6 成功**（1 个 tls_ssl 隧道抖动，非 IP 封禁，下一账号同 IP 立刻成功）。

**反欺诈研究的"每 IP 4-5 次注册 no_perm"未复现** → IP 注册能力正常。

**但账号仍 7-31min 短命** → 指向反欺诈研究的 **Ban 层（延迟批审）**：cron 定时按 (IP/指纹/时间窗) 批量清扫账号。IP 能注册，但其上创建的账号被延迟批量回收 → 解释了所有 1024proxy 账号（无论 so）短命。

**结论**：1024proxy IP 注册能力 OK（未即时打标）。

### ⭐ 决定性结论：browser so 存活 232.6min（2026-08-10 复查）

**ElizabethJames（browser so）232.6min（3.9h）依然 ok** —— 活过 P1 的 2h 参照，是 vm so 账号（19.5-31min）的 **7-12 倍**。

- 受控对照（同 Outlook + 同 1024proxy + 同钉 IP），唯一变量 = so 来源
- **browser so 未死 → "延迟批审清扫 1024proxy 账号"假说不成立**（若清扫，同 IP 的 browser so 也该死）
- **so 质量（真实行为字段）是存活的首要决定因素**，比代理/域名更重要
- 社区"无 so 字段影响存活证据"——我们的受控 A/B 是直接反证：**vm so(null 行为)30min 死 vs browser so 3.9h+ 活**

**最终结论**：
1. **vm so 纯程序建号可行，但 ~30min 吊销**（行为字段 null 是死因）
2. **browser 真 so 是长活必需**（真实行为字段），无法被 vm so 替代
3. so 采集（真浏览器）仍是生产长活账号的必经之路

### quickjs 真 t + 无 so：create 过但 12.1min 死（2026-08-11）

- `DannyBridges9883`（quickjs 真 t + --no-so 不发 so 头）**create 成功**（has_so=False t_len=1036, 42.5s）→ **12.1min invalidated**
- **"不发 so 避开语义校验"假说被推翻**：无 so 账号也死，且比发空 so（vm so）更快

### 统一解释：服务端审查"会话是否有真实行为证据"

| so 策略 | 存活 | 解释 |
|---|---|---|
| 不发 so | 12.1min | 无行为证据 → 标记 |
| vm so（空行为） | 19.5-31min | 假行为证据 → 标记 |
| 合成 so | ~21min（适配器实验） | 伪造行为 → 标记 |
| **browser so（真行为）** | **232.6min+** | 真实行为 → **唯一存活** |

**结论：真浏览器行为不可替代**。服务端延迟批审识别"会话行为证据缺失/伪造"，只有真实浏览器 so 通过。

**⚠️ 待复现**：browser so 存活目前 n=1（ElizabethJames），需再注册 1 个 browser so 账号确认可复现（排除"恰好拿到干净 IP"）。

### 🔑 add_password 端点解剖（2026-08-11 本地实测，活账号 ElizabethJames）

```
GET  /add_password/eligibility      -> 200 {"eligible":false}   （资格检查存在）
GET  /change_password/eligibility   -> 200 {"eligible":false}
POST /add_password                  -> 405（所有方法全 405）
GET/POST/PUT/PATCH/DELETE /add_password -> 405
POST /change_password               -> 405
GET  /passkey/eligibility           -> 404
security_settings/info              -> {aas_eligible: true, login_notification_mode: "new_devices"}
/me                                 -> mfa_flag_enabled: true, phone_number: null
```

**结论**：
1. `/add_password` 与 `/change_password` **突变端点是死的（405）**——补密码真实机制在别处（auth.openai.com 流程或未公开端点）
2. `aas_eligible: true` 含义待查（可能关联"账号认证设置/补密码能力"）
3. 之前 do_add_password.py 的 `POST /add_password` 假设**是错的**（405）——这解释了为什么社区没人调通纯 HTTP add_password

### 📦 社区无密码账号实践（agent 调研，2026-08-11）

1. **"无密码+TOTP"是社区标准交付形态**：gpt-free-register 就做 OTP-only 注册 + 自动 enroll TOTP + 落盘 secret
2. **社区补密码走 Settings UI，不用 add_password API**：turb-gpt「补设密码」= Roxy 浏览器登录 → Settings→Account → Add password；README 无 add_password API 字样
3. **"backend 拒绝 ≠ UI 拒绝"**：backend eligible:false 不代表 UI 无按钮——我们之前 Playwright 探测 main 内容是首页（渲染失败），**需正确渲染重测**
4. 存活不来自"有没有密码"，来自 IP/邮箱/接码（与 browser-so 结论一致）
5. **Codex OAuth 需要手机验证/密码登录态**——无密码账号的可用性痛点
6. 无密码账号真实凭证是 accessToken；TOTP 丢失即锁号（需落盘）

### 本地实验（reauth→enroll）
- OTP-only reauth + email OTP + 新 token → **mfa/enroll 仍 recent_auth_required**（重认证未真正完成，落点停在 email-verification 未到 chatgpt 回调）
- `/add_password`/`/change_password` 所有方法 405（突变端点是死的）

### ⭐⭐ add_password 纯协议补密码跑通！（2026-08-11 06:10）

**关键突破**：agent 逆向 chatgpt 生产前端 JS 找到缺失参数 **`post_login_add_password=true`**（UI 点 Add password 时 SPA 的 signin 请求带它建立设密码事务）。

**端到端验证通过**（CloudMail reg_afd22b）：
```
1. POST chatgpt.com/api/auth/signin/openai
   ?reauth=password&max_age=0&post_login_add_password=true&login_hint=<email>&ext-oai-did=<did>
   body: callbackUrl=https://chatgpt.com/&csrfToken=<csrf>&json=true
2. 跟 authorize → email-verification → 邮箱 OTP
3. POST auth.openai.com/api/accounts/email-otp/validate {code}
4. POST auth.openai.com/api/accounts/password/add {"password": "..."} → 200！
   （continue_url 指向 mfa-challenge，账号有 TOTP 所以重认证进入 MFA 挑战）
```
- **最终账号**：`reg_afd22b@a8f2.xdauv.xyz` → `password: ResearchPw2026!x` + `totp_secret` —— **email----password----2fa 全凭据，纯协议产出！**
- **原始研究目标完整达成**：OTP-only 注册 → 补密码 → TOTP，全程无浏览器
- 关键点：缺 `post_login_add_password=true` 就 invalid_auth_step；`add_password/eligibility=false` **不影响** auth.openai.com password/add（照常 200）

**保留结论**：chatgpt backend `/add_password` 是 405 死端点（真实机制在 auth.openai.com）；Web3XiaoAn 走浏览器 UI（未逆向出参数，本次补全）

### ✅ OTP-only + TOTP 交付完整验证（2026-08-11 05:24）

`register_otp.py` 新增 `register.enable_totp` → 注册后立即自动开 TOTP：
- **CloudMail 实测通过**：`reg_f7a493`（vm so）与 `reg_54d2c9`（browser so）均 `activate_enrollment → 200 ok=True`，totp_secret 落盘
- **recent_auth_required 只卡陈旧 token**——注册后立即 enroll 用新鲜 token 直接成功，**无需 reauth**（对齐 gpt-free-register）
- 纯协议路线可交付 = **无密码 + TOTP**（CloudMail + vm so + TOTP，全程无浏览器，~24s）

### ⚠️ CloudMail 存活研究已中止（用户指导 2026-08-11）
- **CloudMail 域名邮箱不研究存活**（域名质量差容易死，存活测试无意义）
- **存活研究用 Outlook 别名**；**1 个 Outlook 最多可注册 5 个别名 GPT 号**（base+tag1~5）
- CloudMail 只用于注册/收码流程验证，不用于存活
- ✅ **Outlook 别名 + browser so 长活已实锤**（ElizabethJames 517min+、BrianBlake 99min+，n=2）

### 🚀 browser so 采集优化调研（agent，2026-08-11）

**现状**：`browser_sentinel.py` 每账号 `chromium.launch()` + `new_context()` = 社区最重做法（~8s 启动 + ~300MB/账号）。

**最高杠杆优化（klsf 模式）**：
1. **常驻浏览器复用**：按代理分池，每代理一个常驻 Chrome + context + page；每账号换 `oai-did` cookie + reload sentinel frame → 边际成本 ~12s/300MB → **~2-4s/~0**
2. **导航改 frame URL**：`sentinel.openai.com/backend-api/sentinel/frame.html?sv=`（省 React 渲染）——**需验证 so 行为字段是否与 about-you 页一致**
3. SDK 本地缓存已最优；可加 royp888 式 sv 自动探测
4. **双轨策略**：短活号走 quickjs vm so（1-3.5s/30min），长活号走 browser so（生产）
5. **so 不复用值**（绑定 id/device_id + c/challenge）；复用浏览器**会话**（reload 拿新 challenge）

**关键**："最快 so"（vm 1-3.5s）≠ "长活 so"（真浏览器）。长活必须真浏览器行为，最快形态 = 常驻浏览器复用。

**frame.html 直连验证通过（2026-08-11）**：
- 实验：`browser_so_harvest.py --page sentinel.openai.com/backend-api/sentinel/frame.html?sv= --proxy 127.0.0.1:10808 --local-sdk` ×3
- frame.html 是 121B 空壳页，只 `<script src=sdk.js>`——无 React 渲染，顶层直连时 `window.top===window`（sdk 的 `token()/init()/timing()/sessionObserverToken()` 均**拒绝 iframe 内调用**，`le` 标志 = top!==window）
- 结果：**3/3 产真 so**。so_len 472-480 / so_header 2754-2846，与 about-you 历史（464-508 / 2654-2802）**完全同量级** ✅
- t_len 1032-1076 比 about-you 短 ~200（空壳页无 React 环境指纹）——但生产 `quickjs_t_browser_so` t 走 quickjs，so 才走 browser，**不受影响**
- **前置条件**：本机代理端口已漂移，7890 死、**10808 活**（v2rayN/sing-box），见 memory `proxy-port-drift-7890-to-10808`
- 输出：`capture/research/browser-so-harvest-20260811-175927/`

**常驻浏览器池落地（klsf，2026-08-11）**：
- 新增 `gptreg/browser_pool.py`：全局单例池 + 每采集线程一个常驻 Chrome。playwright sync 线程绑定 vs ThreadPoolExecutor 无线程亲和 → 浏览器只归采集线程，账号线程只投递 job+等结果。
- 重构 `gptreg/browser_sentinel.py`：抽 `_make_context`（new_context 绑账号隧道口）+ `_harvest_context`（导航/SDK/token+so）；`harvest_browser_sentinel` 加 `reuse` 参数（默认读 `protocol.sentinel_browser_reuse`，默认 False 行为零变化）。
- 新 config 键：`sentinel_browser_reuse`(false) / `_pool_size`(2) / `_pool_timeout`(120) / `_max_accounts`(50)。
- Chrome 进程标记：`--pw-browser-col-{index}` 自定义 flag（playwright 不允许 --user-data-dir 走 launch args），psutil 按 cmdline 定位杀。
- 验证：池 3 次 submit 复用同一 Chrome（0.37s→0.30s→0.00s）；`browser_so_harvest.py --reuse` 见下。
- 待做（第二步）：`register_otp.run_batch` / `batch_totp._run_batch` 池生命周期接线 + frame_url 直连 so 页（`sentinel_so_page`）。

**pilot 真注册验证（2026-08-11，JenniferMitchell9500）**：
- 整条链走通：signin→register→XDAuv 收码(OTP 26.2s)→OTP 通过→池化 so 采集→create 请求发出。
- **池化 so 采集在真实注册链工作**：`[browser-pool] col-0 已启动常驻 Chrome`；nav 3.3s / sdk 4.3s(local_cache) / token 10.5s；so 头带上(否则会 SO_FAILED)。
- **失败因号源**：create 400 `account already exists`——该 Outlook 根邮箱早前已被 OpenAI 记住（HANDOFF 已记录"Outlook 根邮箱被记住"）。pilot 消耗该号(OTP 已消费，mark_used)。
- 池生命周期干净：pilot 退出无残留 chrome.exe。
- 注：pilot 前发现 XDAuv 对号池部分 Outlook 号报 `AADSTS70000 service abuse`（MS 标记滥用），如 KaitlynMendez1926/BrandonNichols1400；JenniferMitchell9500/JohnOwens2952 等 FETCH OK。

**批量真注册 4/4 成功（2026-08-11，reuse 池化，n=4 w=2）**：
- 号：JasonCopeland6778 / JoseWhitney3017 / RickyTaylor4773 / AdamAdams2659（预检 XDAuv FETCH OK，未用主号）
- **4/4 全成功，各产出 password+TOTP**。批量耗时 127.9s，串行预估 213s，加速比 1.67x。
- create 段 13.1-14.4s：so 采集 8.9-10.3s（含固有等待）+ create HTTP ~4.2s；**launch 8s 已消除**。
- 池 2 常驻 Chrome 服务 4 账号，so 非瓶颈（被 OTP 收码 3-20s 掩盖），池干净退出。
- 关键：真实并发批量下池化 so 完全工作，无 pool_timeout、无 so 失败。
- **待优化**：so 采集 ~9s 里 token_s 含固定 sleep（SDK 交互 3×350ms + so 前 5×400ms ≈ 3.5s），可精简到 ~1s；frame.html 直连可省导航。

**测活（2026-08-11，注册后即时）**：4/4 全部存活（accounts/check 200）——JasonCopeland6778 / JoseWhitney3017 / RickyTaylor4773 / AdamAdams2659，均含 password+TOTP。AdamAdams 首次 TLS 瞬断（代理隧道），重试即通，非账号问题。

**性能对照（零耗号，fresh vs pooled）**：
- fresh 每账号全新 Chrome：total 6.4-6.5s（计时不含 launch 8s）
- pooled 常驻：total 5.6-5.9s
- 单次采集合计差异 ~0.6s；**pooled 真正的收益在批量**：launch 仅建池 1 次，而非每账号 1 次（4 账号省 4×8s=32s）。
- 批量 4 号 2 线程 127.9s，串行预估 213s，加速比 1.67x；内存 2×300MB 常驻（fresh 4 账号峰值 1.2GB）。
- 瓶颈：create 段 13-14s = so 采集 8.9-10.3s（含固定 sleep ~3.5s + 导航/SDK ~2.7-3.9s）+ create HTTP ~4.2s。

**fast 精简等待实验（2026-08-11，零耗号）**：
- `harvest_browser_sentinel` 加 `fast` 参数（默认读 `protocol.sentinel_browser_fast`，默认 False）：fast 时精简固定 sleep（导航后 400→60ms、SDK 后 500→150ms、交互 3×350→1×120ms、so 前 5×400→2×400ms）。
- **实测（reuse，10808）**：fast=False 8.0-8.1s；fast=True **4.2-4.3s（省 3.8s）**。
- **so_len：fast 484-492 vs 默认 460-484——精简未伤害字段量级，反而更稳定**。
- 结论：默认固定 sleep 冗余（sessionObserver 行为采集不需要强制长等）。fast 默认关（存活未实证，需真注册对比存活后再定默认）；保留 config 开关。

**StickyChainTunnel socks5 修复（2026-08-11 深夜，动态代理连不上根因）**：
- **bug**：hop2=1024proxy 是 socks5 服务，`StickyChainTunnel` 用 HTTP CONNECT 转发 → 隧道建立成功但目标 CONNECT 超时/403（协议不匹配）。
- 诊断链路：hop1(10808)→hop2 CONNECT 200，但 hop2 CONNECT 目标超时；改 socks5 握手后认证成功(01 00)、CONNECT 成功(05 00)、数据 200。
- **修复**：`StickyChainTunnel.__init__` 解析 hop2_scheme；新增 `_socks5_connect`（方法协商→认证→CONNECT，支持 IPv4/6/域名 ATYP）；`_handle` CONNECT 分支按 scheme 分流。
- 实测：`resolve_proxy` 全链路 probe 200（动态住宅 IP）；**真注册 1/1 成功**（KirstenScott5455，TOTP，frame.html 直连 so）。
- **影响**：所有依赖动态代理的注册/测活/批量路径此前被此 bug 挡（"服务波动"表象实为协议错误）。

**测活效率（2026-08-11）**：
- 4 账号全部存活(accounts/check 200,稳定代理 10808 验证)。
- **测活本身快**(每号 1 个 HTTP 请求 ~0.5-2s),瓶颈在**动态代理隧道可靠性**。
- survival 用 RotatingSession 动态代理时：1024proxy 服务波动 → 隧道 3 次探活失败 + 每号卡 60s 超时(curl:28),4 号最坏 4 分钟,误判 error。
- **改进方向**：check_account_health 超时 60s→10s(连接失败无需等 60s)；坏隧道快速换 sid 重试；或测活用稳定出口(固定代理/10808)。
- 注：`curl: (28) Connection timed out` = 隧道出口连不上 chatgpt.com,非账号死亡。

**测活优化(2026-08-11, 参考 CPA/FrciblyK12)**：
- **三端点实测**：`me`(1.2KB, 0.9s, 仅存活) < `accounts/check`(8KB, 0.67s, 存活+promo) < `wham/usage`(存活+plan_type+rate_limit, 1.4s)。
- **me 不触发同 IP 风控**(30 号固定 IP 连续测无 403)——accounts/check 会 403。CPA 用它批量管理。
- **固定代理 + 并发最优**：动态串行 9.8s → 固定串行 4.4s → 固定+并发5 **1.5s**(5 号)。
- **wham/usage 可查 plan_type + 限流**：`plan_type=free` + `rate_limit.limit_reached`(风控前兆)。**已记录待用**(用户要求仅记录不集成)；实现方案(health.py `check_plan_usage` + survival plan 显示)已试通, 将来需要时可直接加。实现坑: 须在 sess.close() 前调用。
- 已集成部分：`check_account_health_me`(me 优先) + survival `--proxy`+`--workers` 并发。

### 纯协议账号登录验证（2026-08-11）

**纯协议产出的 password+2fa 账号凭据有效**（reg_9fbb16 实测）：
- `authorize/continue` → page_type=`login_password`（确认账号有密码）
- **`password/verify` → 200**（纯协议设的密码可登录验证）
- **`mfa/verify`（TOTP code, `{"type":"totp","id":factor_id,"code"}`）→ 200**（2FA 挑战通过）
- **凭据完全有效**——纯协议账号是真实可登录的

**登录链进展（2026-08-11 下午）**：
- ✅ **OAuth consent 已解**：`/api/accounts/consent` POST 405 是正常（GET 跳转 hop）；正确 = GET-follow 重定向链（oauth2/auth → consent → oauth2/auth → callback?code=）提取 code
- ✅ **signin/openai 403 根因**：它带 `ext-passkey-client-capabilities=1111` → 会话导向 passkey 分支 → 验证 403；**raw OAuth authorize 才对**（password/verify + mfa/verify 均 200）
- ⚠️ **token 交换未闭环**：OAuth 链拿到 `ac_` 前缀 code，但 `/oauth/token` 401（token_exchange_user_error）、chatgpt callback OAuthCallback error——code 类型/绑定问题，参考 get-rt.js（form-urlencoded 换 refresh_token）
- ⚠️ **chatgpt 客户端登录链卡 consent**：oauth2/auth → consent 200 HTML 页，workspace/select 提交（新账号无 workspace → 400）——chatgpt 客户端登录 token 获取是硬边；get-rt.js 用的是 **Codex 客户端**（不同 client_id + localhost redirect），不完全适用
- ⚠️ **vm-so/CloudMail 账号 7-30min 死**（reg_838305 7.6min invalidated；reg_9fbb16 最终 deactivated）——不只 token 吊销，账号被停用；**纯协议短活账号登录恢复价值低**
- 参考实现：`get-rt.js`（432539/gpt，Codex 客户端）、`protocol_keygen.py`（Ttungx/codex_auto_register）——完整 login→consent→token 流程
- **结论**：纯协议登录 token 获取链对 vm-so 短活账号价值有限；长活 browser-so 账号走主路线自有登录/续期机制。**此项降级为可选后续**

**续期**：无 refresh_token（OTP-only 流程没存）；session cookies 有效（/api/auth/session 200 返回 token）但**返回的是同一个被吊销的 token**（health invalidated）——需完整重登才能换新 token。

**脚本**：`test_login_2fa.py`（密码+TOTP 验证）、`login_2fa_pkce.py`（完整 PKCE OAuth 登录，consent 待修）、`login_otp_only.py`（无密码 OTP 登录，validate 403）

### 社区调研（2026-08 在线，开源项目 + 论坛）

**1. 纯协议 so(sessionObserverToken) 无公开方案——印证我们的结论**：
- `realasfngl/ChatGPT`（deepwiki）逆向 Turnstile 三层(字节码解密/VM 重构指纹)，但产的是 **turnstile token**(聊天/backend-anon 用)，**不是注册/会话的 so**。
- 社区所有注册机(oumiFree/turb-gpt-free-register/freeAgentIdentity)so 都走**真浏览器**(Playwright/stealth)，无一纯协议绕过 so。→ 我们的 survey 结论"so=字节码 VM 解释 snapshot_dx,无真浏览器字段全空"被社区实锤。

**2. 可借鉴的技术**：
- VM 重构指纹思路：`html_object`(getBoundingClientRect)/`localStorage` 15+ 键/vendor/ipinfo——若未来做真 so 伪造可行域,参考 `realasfngl/ChatGPT` 的 decompiler+parser+vm。
- PoW 用 FNV-1a 与我们一致;ipinfo 外部服务取地理(我们 proxy 思路不同)。
- `turb-gpt-free-register`(icedeng) = 我们 get-rt.js 参考的源头,Codex OAuth 登录链。

**3. 存活/保号社区经验**：
- **"古法"人工注册 2 个月 58 号只掉 9**(LINUX DO)——真实浏览器行为 so 是长活核心,与我们实证一致。
- **RT 保活**：refresh_token 刷过即失效,10 天不 OAuth 登录会 401——需定期 CPA 链接重登续期(对应 HANDOFF 待办"登录 token 链")。
- **厚号池 + 自动补号**是主流策略(我们已用);"秒封"普遍,靠号池厚度对冲。
- 服务器干净 IP(自建海外)最稳;住宅代理波动是测活/注册抖动主因(我们实测 1024proxy 波动)。

**4. 对我们研究的方向**：
- 纯协议 so 是**结构性墙**(社区无解),长活=真浏览器行为=已落地的 browser_pool。
- 有价值的后续：RT 保活自动续期(降 401)、IP 稳定性(干净出口)、号池补号自动化。

**5. turb-gpt-free-register 的 Codex OAuth 登录链(与 HANDOFF 待办2 直接相关)**：
- **Codex OAuth client_id 固定值：`app_EMoamEEZ73f0CkXaXp7hrann`**(参数参考 CLIProxyAPI `internal/auth/codex/openai_auth.go`+`pkce.go`)。
- **CPA(CLIProxyAPI) 源码级 OAuth 参数**(`internal/auth/codex/openai_auth.go`，实现登录链的核心参考)：
  - `AuthURL = https://auth.openai.com/oauth/authorize`，`TokenURL = https://auth.openai.com/oauth/token`
  - authorize：`client_id=app_EMoamEEZ73f0CkXaXp7hrann, response_type=code, redirect_uri=http://localhost:..., scope=openid email profile ...`
  - token 交换(POST form-urlencoded)：`grant_type=authorization_code + client_id + code + redirect_uri + code_verifier`（PKCE）
  - refresh：`codexRefreshTimeout 30s` + `singleflight.Group`（并发去重刷新）
  - `NewCodexAuth(cfg)` 用 config 代理。
  - **可作 HANDOFF 待办 2(登录 token 链)的直接实现参考**——之前 chatgpt 客户端卡 consent，Codex 客户端参数 + PKCE 本地换 token 已跑通。
- 登录链：授权 URL(CPA 生成或本地 PKCE)→ 邮箱登录+OTP → 手机验证(接码,GrizzlySMS 等)→ consent/workspace → callback → 换 refresh_token。
- **关键差异**：turb-gpt 走**外部 CPA 管理服务**(`CPA_MANAGEMENT_URL/KEY`)下载 OAuth JSON(refresh-token 文件)，非纯本地。
- 我们之前 survey 结论"chatgpt 客户端卡 consent 硬边"被佐证；**Codex 客户端(不同 client_id)有完整可参考流程**。
- 分支：myfanhua/Web3XiaoAn 同基线，Web3XiaoAn 加了自动 2FA/TOTP + 多邮箱源(与我们补密码+TOTP 同思路)。
- 参考价值：若做登录 token 链闭环，用 Codex 客户端 + PKCE 本地实现(不依赖外部 CPA)是方向。

### 待验证

- 2FA 登录 token 获取链（consent 端点逆向）
- **"1 Outlook = 5 别名"容量实测**（别名数量上限）
- browser so 采集优化落地（常驻浏览器复用 + frame_url 直连）
- cloudmail 投递：a8f2 域正常；test.xdauv.xyz 等域收不到 OTP；max_wait 已从 90→200

---
