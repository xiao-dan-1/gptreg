# GPT 协议注册机 —— 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              入口层 (capture/)                              │
│                                                                             │
│  单号注册            批量生产           运维工具                              │
│  verify_pwd_totp.py  batch_totp.py     check_imap.py / check_survival.py    │
│  (CLI 薄壳: 选主号   (复用核心循环,    backfill_token.py / check_raw_tokens  │
│   →别名→调核心→      subprocess 无,    login_pwd_check_totp.py               │
│   按 outcome 打印)   按失败类型管主号)  main.py (OTP-only 旧流水线)          │
└──────────────┬──────────────────┬───────────────────────────────────────────┘
               │                  │
               ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         核心注册链 (gptreg/register_pwd.py)                  │
│                                                                            │
│  register_account(cfg, account, *, email, password, name, bday, proxy)     │
│       → RegistrationResult(outcome, email, diag, record)                   │
│  outcome: SUCCESS | IP_BLOCKED | MAIL_REGISTERED | SO_FAILED |              │
│           OTP_FAILED | CREATE_FAILED | SESSION_FAILED | HEALTH_FAILED |     │
│           ENROLL_FAILED                                                    │
└──────┬───────────────┬───────────────┬──────────────┬──────────────────────┘
       │               │               │              │
       ▼               ▼               ▼              ▼
┌─────────────┐ ┌──────────────┐ ┌───────────┐ ┌──────────────┐
│ 协议层       │ │ Sentinel 层   │ │ 邮件层     │ │ 代理层        │
│ gptreg/auth │ │ (产真t+真so)   │ │ mail/     │ │ proxyutil.py │
└──────┬──────┘ └──────┬───────┘ └─────┬─────┘ └──────┬───────┘
```

## 分层明细

### 1. 入口层 `capture/`
| 脚本 | 职责 |
|---|---|
| `verify_pwd_totp.py` | 单号注册 CLI 薄壳：选主号 → 生成别名/密码/姓名 → 调 `register_account` → 按 outcome 打印反馈 |
| `batch_totp.py` | 批量：循环调核心（无 subprocess），按失败类型管主号生命周期（不烧号） |
| `check_imap.py` | 号池 IMAP 可用性检查（决定收码走快通道还是 Graph 降级） |
| `check_survival.py` | 从 accounts.jsonl 读 token 测活 |
| `check_raw_tokens.py` | 直接喂 JWT 测活（不经 accounts.jsonl） |
| `backfill_token.py` | 补缺失 access_token（密码+TOTP 登录） |
| `main.py` | OTP-only 旧流水线入口（非当前主路线） |

### 2. 核心注册链 `gptreg/register_pwd.py`
`register_account()` 单函数封装整条链，CLI/批量共享，返回结构化 Result（outcome 决定主号"可重试 vs 永久弃用"）。

### 3. 协议层 `gptreg/auth.py`
```
signin_openai → follow_authorize(落点诊断) → register(设密码)
→ send_otp → validate_email_otp → create_account → follow_oauth_callback
→ fetch_session → mfa/enroll → activate_enrollment (2FA 激活链)
```
- 依赖 `session.BrowserSession`（请求会话，Device-ID + OAI-SC cookie）
- 400 自动换 sid 重试逻辑在 register_pwd，不在 auth

### 4. Sentinel 层（产真 t + 真 so）
| 引擎 | 文件 | 产物 |
|---|---|---|
| quickjs | `sentinel_quickjs.py` | Node VM 跑官方 sdk.js 产真 t（Node 18+） |
| browser | `browser_sentinel.py` | 真 Chrome `sessionObserverToken` 采真 so |
| pow | 纯 Python | OTP-only 流水线用 |
| 注册表 | `sentinel_engine.py` | 按策略分发到上述引擎 |

> **硬约束**：禁止假 so / 假 finalize。假 t ~6h 吊销；真 t+真 so 才能长期存活。
> create 阶段 quickjs t + browser so **并行采集**（独立线程），so 失败重试 3 次 + 中止。

### 5. 邮件层 `gptreg/mail/`
```
抽象基类 (base.py):
  MailClient.wait_for_otp()   ← 收码能力
  MailSource.parse_line()     ← 号池行解析

插件注册表 (sources.py + providers.py):
  MAIL_SOURCES / MAIL_CLIENTS → build_mail_client(account) 工厂分发
```
| 插件 | 文件 | 号池格式 |
|---|---|---|
| IMAP | `imap.py` | `email----pass----client_id----refresh_token` (XOAUTH2) |
| Graph | `ms_graph.py` | 同 ms_oauth，降级兜底 |
| 外部 | `external.py` | XDAuv 服务 + Gmail API |
| API | `api.py` | `email----api_key` 配 `mail.api_client` |
| CloudMail | `cloudmail.py` | 单段邮箱 `user@domain`，admin 拉码 |
| 缓存/身份 | `otp_cache.py` | OTP 去重、身份键、时间 |
| 号池状态机 | `pool.py` | claim/mark_used/mark_failed + TTL 自动回退 |

### 6. 代理层 `gptreg/proxyutil.py`
```
pick_proxy → build_dynamic_proxy (cliproxy: region/sid/粘性)
→ needs_chain → StickyChainTunnel (client→chain_via 7890→CONNECT cliproxy→目标)
→ ResolvedProxy(session_url, upstream, chain)
```
- 动态住宅 IP 必须（数据中心 IP 被风控）
- 换 sid = 换出口 IP；单次注册隧道粘性固定

### 7. 支撑层
| 模块 | 职责 |
|---|---|
| `config.py` | YAML 配置加载 + 默认值深合并 |
| `session.py` | `BrowserSession`：HTTP 会话、device_id、OAI-SC、headers 构造 |
| `store.py` | 统一落盘 `accounts.jsonl`（去重 upsert） |
| `health.py` | `check_account_health`（秒封检测 / 测活） |
| `postlogin.py` | 登录后 me + conversation/init + prepare（不造假） |

## 数据流（单号注册）

```
capture/verify_pwd_totp.py
   │  ① 选主号(号池) + 生成 别名邮箱(+tag)/密码/姓名/生日
   ▼
register_account()                        resolve_proxy() → 住宅代理
   │                                             │
   ├─► auth.signin_openai → follow_authorize ────┤ (400→换sid重试3次)
   ├─► register(设密码) quickjs 真t ─────────────┤
   ├─► send_otp → build_mail_client → wait_for_otp┤ (IMAP快/Graph慢)
   ├─► validate_email_otp ───────────────────────┤
   ├─► create_account: quickjs真t ‖ browser真so ──┤ (并行, so失败中止)
   ├─► follow_oauth_callback → fetch_session ────┤
   ├─► check_account_health (秒封检测) ───────────┤
   ├─► mfa/enroll → activate_enrollment (2FA) ────┤ (复用注册隧道)
   └─► store.save_account → accounts.jsonl        │
```

## 号池生命周期（批量）

```
mail_pool.txt ──parse_mail_line──▶ MailPool.claim()
    │                                    │
    │                                    ▼ outcome
    │                              ┌─ SUCCESS        → mark_used
    │                              ├─ IP_BLOCKED/    → mark_failed(≤3次, 不烧号)
    │                              │  OTP_FAILED/      (基建可重试, TTL 30min 回退)
    │                              │  CREATE_FAILED/
    │                              │  SO_FAILED
    │                              ├─ MAIL_REGISTERED → 永久弃用(记 totp_failed)
    │                              └─ ENROLL_FAILED  → 账号已建, 2FA 可后补
    ▼
mail_pool.txt.state.json  (used/bad/failed + TTL)
```
