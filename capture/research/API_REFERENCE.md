# GPT 协议注册机 API 参考

> 集中记录研究过的 OpenAI/ChatGPT/Microsoft/CloudMail 相关 API。
> **用途**：测活/注册/登录/收码各流程的端点、请求、响应、注意事项。
> **维护**：研究新 API 时往对应分组追加；发现响应变化时更新"注意"。

---

## 一、认证 / 注册(auth.openai.com)

| 端点 | 用途 | 方法 | 关键参数/注意 |
|---|---|---|---|
| `/api/auth/providers` | 获取登录 providers | GET | 注册前置 |
| `/api/auth/csrf` | 获取 CSRF token | GET | 后续请求需带 |
| `/api/auth/signin/openai` | OpenAI 登录入口 | POST | **带 `ext-passkey-client-capabilities=1111` 会导向 passkey 分支 → 403**；raw OAuth authorize 才对 |
| `/api/auth/session` | 会话/token | GET | 返回 access_token + session_token |
| `/api/accounts/user/register` | 注册(设密码) | POST | 需 sentinel；400 落 log-in=邮箱已注册 |
| `/api/accounts/email-otp/send` | 发邮箱 OTP | POST | |
| `/api/accounts/email-otp/validate` | 验证 OTP | POST | 通过后落 about-you |
| `/api/accounts/authorize/continue` | authorize 推进 | POST | |
| `/api/accounts/create_account` | 建账号 | POST | 需 sentinel t + so 头 |
| `/api/accounts/password/add` | 补设密码 | POST | **signin 需带 `post_login_add_password=true`**，缺则 invalid_auth_step |
| `/api/accounts/password/verify` | 验证密码 | POST | 登录链用 |
| `/api/accounts/mfa/verify` | TOTP 验证 | POST | `{"type":"totp","id":factor_id,"code"}` |
| `/about-you` | 注册信息页 | GET | sentinel 采集页(默认) |

## 二、OAuth / 登录链

| 端点 | 用途 | 关键参数 |
|---|---|---|
| `auth.openai.com/oauth/authorize` | OAuth 授权页 | `client_id`+`response_type=code`+`redirect_uri`+`scope`+PKCE |
| `auth.openai.com/oauth/token` | code 换 token | `grant_type=authorization_code`+`code`+`code_verifier` |
| `auth.openai.com/api/accounts/consent` | consent 授权 | **POST 405 正常**；正确=GET-follow 重定向链提取 code |
| `chatgpt.com/api/auth/callback/openai` | callback 落点 | 提取 code |
| `login.live.com/oauth20_token.srf` | MS OAuth token | Outlook 邮箱池取件用 |
| `login.microsoftonline.com/consumers/oauth2/v2.0/token` | MS token 刷新 | |

**Codex OAuth(CPA 参考)**：
- ClientID = `app_EMoamEEZ73f0CkXaXp7hrann`
- token 交换：`POST oauth/token` form-urlencoded + `code_verifier`(PKCE)
- refresh：singleflight 去重 + 30s 超时(CLIProxyAPI `internal/auth/codex/openai_auth.go`)

## 三、存活测活(chatgpt.com/backend-api)

| 端点 | 用途 | 判定 | 注意 |
|---|---|---|---|
| `/me` | **存活主判定** | 200=ok；401+`code:account_deactivated`=封号；`token_invalidated`=吊销；`token_expired`=过期 | **同 IP 并发不触发 WAF**；1.2KB |
| `/wham/usage` | plan + 限流 | 200 返回 `plan_type`+`rate_limit` | 查 plan(free/plus/expired)+ 限流信号；**已记录待用** |
| `/accounts/check/v4-2023-04-27` | 优惠资格 | 200；含 `eligible_promo_campaigns`/`offers` | **主用途 eligibility 查 promo**；同 IP 连续会 WAF 403(8KB) |
| `/promo_campaign/check_coupon?coupon=...` | 优惠券资格(显式 `eligible`) | 200；`eligible`/`state` | register-kit 对齐；比 accounts/check 更直接，稳定性待 A/B 验证 |

**判定优先级**：me 快判(ok/封号/吊销/过期)→ 需 promo 用 accounts/check。

## 四、2FA / MFA

| 端点 | 用途 | 注意 |
|---|---|---|
| `/backend-api/accounts/mfa/enroll` | 开启 TOTP / recovery | body `{"factor_type":"totp"}` 或 `{"factor_type":"recovery_code"}`；需 fresh token(recent_auth)。factor_type 全集：`totp/recovery_code/email/sms/push_auth/passkey` |
| `/backend-api/accounts/mfa/user/activate_enrollment` | 激活因子 | TOTP：提交 6 位码；**recovery_code：提交整个 30 字符 recovery key** |
| `/backend-api/accounts/mfa_info` | 2FA 状态 | `factors.totp[].is_recovery`；**不显示 recovery 因子**(登录 MFA 挑战 page.payload.factors 才显示) |
| `/api/accounts/mfa/verify` | 登录 MFA 挑战 | `{"type":"totp"\|"recovery_code","id":<因子id>,"code":...}`；recovery 须用 recovery 因子自己的 id |

## 五、Sentinel(防机器人)

| 端点 | 用途 | 注意 |
|---|---|---|
| `sentinel.openai.com/sentinel/{sv}/sdk.js` | SDK(本地缓存) | 按 sv 缓存到 tempdir |
| `sentinel.openai.com/backend-api/sentinel/frame.html?sv=` | so 采集页 | 121B 空壳页只加载 sdk；**顶层直连 `window.top===window`**，token()/sessionObserverToken() 拒绝 iframe 内调用 |
| `sentinel.openai.com/backend-api/sentinel/req` | 拉 challenge | 返回 token/turnstile/proofofwork |
| `chatgpt.com/backend-api/sentinel/chat-requirements/prepare` | chat 需求 | |

**so 采集**：browser_pool 常驻 Chrome + frame.html 直连 + fast 精简等待 → 4.3-4.5s。

## 六、其他

| 端点 | 用途 |
|---|---|
| `chatgpt.com/backend-api/conversation/init` | 会话初始化 |
| `chatgpt.com/backend-api/subscriptions` | 订阅详情(free 返回 404) |
| `chatgpt.com/backend-api/payments/checkout` | 下试用单(服务端判定层) | 返回 `checkout_session_id`；前缀 `oaics_`=OAICS 真资格 / `cs_`/`cslive`=普通 Stripe |
| `api.openai.com/v1` | OpenAI API |
| `api.openai.com/profile` / `/auth` | 个人页/认证 |

## 七、Microsoft 收码(号池)

- Outlook `ms_oauth`：号池行 `email----password----client_id----refresh_token`
- XDAuv 服务收码：`outlook.xdauv.xyz/api/fetch`(海外干净 IP，解决本地 IMAP 被 MS 拒)
- **注意**：部分 Outlook 号被 MS 标 `AADSTS70000 service abuse`(XDAuv fetch 401)——注册前预检(memory `xdauv-pool-precheck-before-register`)

---

## 快速定位速查

| 需求 | 端点 |
|---|---|
| 判存活 | `/backend-api/me` |
| 查优惠资格 | `/backend-api/accounts/check/v4-2023-04-27` |
| 查优惠券资格(显式 eligible) | `/backend-api/promo_campaign/check_coupon?coupon=...` |
| 查 plan/限流 | `/backend-api/wham/usage` |
| 补设密码 | `auth.openai.com/api/accounts/password/add` |
| 登录(token) | `oauth/authorize` → `oauth/token` |
| 开 2FA | `mfa/enroll` → `mfa/user/activate_enrollment` |
| 收码 | XDAuv `/api/fetch` 或本地 IMAP |
| so 采集 | `sentinel.openai.com/.../frame.html` |
