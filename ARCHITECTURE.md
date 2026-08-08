# GPT 协议注册机 —— 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                入口层 capture/ (tools 运维 / legacy 旧路径 / research 研究)     │
│                                                                             │
│  tools/ 单号注册     批量生产       测活/续期       资产视图                    │
│  verify_pwd_totp   batch_totp    check_survival*  account_overview          │
│  (CLI 薄壳: 选号   (复用核心,     refresh_at      backfill_token             │
│   →别名→调核心→     按outcome      login_check   check_raw_tokens            │
│   按outcome打印)    管主号不烧号)                 check_imap                  │
│  legacy/ verify_*(旧路径参考)   research/ probe_*/t_*_exp/so_*(研究)         │
└──────────────┬──────────────────────┬───────────────────────────────────────┘
               │  密码+TOTP 主路线      │  OTP-only 并行路径
               ▼                      ▼
┌─────────────────────────┐   ┌───────────────────┐
│ register_pwd.py (核心)  │   │ register_otp.py   │
│ register_account()      │   │ register_one/     │
│ → RegistrationResult    │   │ run_batch/        │
│  (outcome/diag/record)  │   │ classify_result   │
│  阶段序列: signin→       │   │  (经 main.py/cli) │
│   register→wait_otp→    │   └────────┬──────────┘
│   create→session→enroll │            │(共享 auth)
└─────────┬───────────────┘            │
          │                            │
          ▼                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         共享支撑层 (gptreg/)                                │
│ 协议 auth.signin_flow │ Sentinel 引擎 │ 收码 mail/ │ 代理 proxyutil │ 账号   │
│ (协议步骤内聚, 无散落  │ (quickjs真t   │ (IMAP/    │ (cliproxy住宅+ │ store  │
│  sleep)               │  browser真so) │ Graph/    │  chain隧道)    │ 落盘)  │
│  session/config/health/postlogin                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

**账号管理闭环**（产出账号 → 可维护资产）:
```
accounts.jsonl(分组字段) ←测活回写← check_survival_batch
     │  ↑续期回写(refresh_at: session_token 过期前换新)
     ▼  └─按号源存活率 / 吊销时长分布 / 资产视图(account_overview)
```

## 分层明细

### 1. 入口层 `capture/`（tools 运维 / legacy 旧路径 / research 研究 三层）
| 脚本(tools/) | 职责 |
|---|---|
| `verify_pwd_totp.py` | 单号注册 CLI 薄壳：选主号 → 生成别名/密码/姓名 → 调 `register_account` → 按 outcome 打印反馈 |
| `batch_totp.py` | 批量：循环调核心（无 subprocess），按失败类型管主号生命周期（不烧号） |
| `check_imap.py` | 号池 IMAP 可用性检查（决定收码走快通道还是 Graph 降级） |
| `check_survival*.py` | 账号测活（单/批量, 回写 health_status） |
| `refresh_at.py` | access_token 续期（过期前跑） |
| `account_overview.py` | 账号资产总览 |
| `check_raw_tokens.py` | 直接喂 JWT 测活（不经 accounts.jsonl） |
| `backfill_token.py` | 补缺失 access_token（密码+TOTP 登录） |
| `main.py` | OTP-only 旧流水线入口（非当前主路线） |

### 2. 核心注册链（两条并行路径）
| 路径 | 文件 | 入口 |
|---|---|---|
| **密码+TOTP（主路线）** | `gptreg/register_pwd.py` | `verify_pwd_totp` / `batch_totp` |
| OTP-only | `gptreg/register_otp.py` | `main.py` → `cli.py` |

`register_pwd.register_account()` 封装整条链，返回结构化 `RegistrationResult(outcome/diag/record)`（outcome 决定主号"可重试 vs 永久弃用"）。阶段序列：
```
_signin → _register(设密码, 400换sid重试) → _wait_otp(收码, 通道归因)
→ _create(t+so并行) → _session → _enroll_totp(2FA 激活) → 落盘
```

### 3. 协议层 `gptreg/auth.py`
```
signin_flow(协议步骤内聚: providers→CSRF→signin→authorize, 节奏在auth内)
→ register(设密码) → send_otp → validate_email_otp → create_account
→ follow_oauth_callback → fetch_session → mfa/enroll → activate_enrollment
```
- 依赖 `session.BrowserSession`（请求会话，Device-ID + OAI-SC cookie）
- **协议节奏(sleep)内聚在 `signin_flow`**，调用方不再散落硬编码 sleep
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
| iCloud+XDAuv | `icloud_xdauv.py` | `email----URL` 接码, 限 @icloud.com/@me.com |
| API | `api.py` | `email----api_key` 配 `mail.api_client` |
| CloudMail | `cloudmail.py` | 动态生成邮箱 `reg_xx@域名`（不依赖号池） |
| 缓存/身份 | `otp_cache.py` | OTP 去重、身份键、时间 |
| 号池状态机 | `pool.py` | claim/mark_used/mark_failed + TTL + 账号表联动 |

**收码代理策略**：仅 `ms_oauth`(Outlook IMAP/Graph)走链式隧道；iCloud/CloudMail/API **直连**（第三方/自托管服务本身干净，套隧道反而 TLS 失败）。

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
| `account_store.py` | 统一落盘 `accounts.jsonl`（去重 upsert）+ 测活/续期回写 |
| `health.py` | `check_account_health`（秒封检测 / 测活） |
| `postlogin.py` | 登录后 me + conversation/init + prepare（不造假） |

## 数据流（单号注册）

```
capture/tools/verify_pwd_totp.py
   │  ① 选主号(号池/动态生成) + 别名或主邮箱
   ▼
register_account()                        resolve_proxy() → 住宅代理
   │                                             │
   ├─► auth.signin_flow(协议步骤内聚) ───────────┤ (400→换sid重试3次)
   ├─► register(设密码) quickjs 真t ─────────────┤
   ├─► send_otp → build_mail_client → wait_for_otp┤ (Outlook走隧道/iCloud直连)
   ├─► validate_email_otp ───────────────────────┤
   ├─► create_account: quickjs真t ‖ browser真so ──┤ (并行, so失败中止)
   ├─► follow_oauth_callback → fetch_session ────┤
   ├─► check_account_health (秒封检测) ───────────┤
   ├─► mfa/enroll → activate_enrollment (2FA) ────┤ (复用注册隧道)
   └─► account_store.save_account → accounts.jsonl│
```

## 账号管理闭环（产出账号 → 可维护资产）

```
accounts.jsonl(分组字段: 身份→凭据→设备→状态→观测→运维)
   │  注册落盘
   ├──测活── check_survival_batch (定期换IP) → 回写 health_status + last_checked
   ├──续期── refresh_at (过期前) → 回写 access_token + session_token + expires
   ├──视图── account_overview (总数/存活/按号源存活率/吊销时长分布)
   └──号池── MailPool 与账号表联动(反查已注册主号)
```

## 号池生命周期（批量）

多号源: Outlook(mail_pool.txt) / iCloud(icloud_pool.txt) / CloudMail(--pool cloudmail 动态生成)

```
号池文件 ──parse_mail_line──▶ MailPool.claim() ──反查账号表──▶ 已注册主号并入 used
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
<pool>.state.json  (used/bad/failed + TTL)
```
