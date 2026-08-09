# GPTReg —— 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                  统一 CLI 入口 main.py (gptreg/cli.py 路由器, 子命令式)          │
│  gptreg/commands/ 每命令 add_parser + run(cfg, args):                         │
│  register(OTP) check-proxy stats overview export survival refresh             │
│  backfill imap raw-check                                                       │
│  capture/tools/ 未迁移: verify_pwd_totp(主路线) batch_totp(批量)               │
│   login_pwd_check_totp check_totp_status(Playwright) check_survival refresh_health │
│  capture/legacy/(旧路径参考)  capture/research/(probe_*/t_*_exp/so_* 研究)      │
└──────────────┬──────────────────────┬───────────────────────────────────────┘
               │  密码+TOTP 主路线      │  OTP-only 并行路径
               ▼                      ▼
┌─────────────────────────┐   ┌───────────────────┐
│ register_pwd.py (核心)  │   │ register_otp.py   │
│ register_account()      │   │ register_one/     │
│ → RegistrationResult    │   │ run_batch/        │
│  (outcome/diag/record)  │   │ classify_result   │
│  阶段序列: signin→       │   │  (经 register 子   │
│   register→wait_otp→    │   │   命令)           │
│   create→session→enroll │   └────────┬──────────┘
└─────────┬───────────────┘            │
          │                            │
          ▼                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         共享支撑层 (gptreg/)                                │
│ 协议 auth.signin_flow │ Sentinel 引擎 │ 收码 mail/ │ 代理 proxyutil │ 账号   │
│ (协议步骤内聚, 无散落  │ (quickjs真t   │ (IMAP/    │ (cliproxy住宅+ │ store  │
│  sleep)               │  browser真so) │ Graph/    │  chain隧道,    │ 落盘+  │
│  session/config/health/postlogin    │ iCloud URL/│  探活重建)      │ 自动   │
│                                     │ CloudMail) │                 │ 备份   │
└─────────────────────────────────────────────────────────────────────────────┘
```

**账号管理闭环**（产出账号 → 可维护资产）:
```
accounts.jsonl(分组字段, 自动备份)
   ↑ 注册落盘 (register_pwd)
   ├─测活→ main.py survival → 回写 health_status + last_checked
   ├─续期→ main.py refresh → 回写 access_token + session_token + expires
   ├─视图→ main.py overview → 总数/存活/按号源存活率/年龄分布
   ├─导出→ main.py export → email----password----2fa[----at]
   └─号池→ MailPool 与账号表联动(反查已注册主号)
```

## 分层明细

### 1. 统一入口 `main.py` + `gptreg/commands/`（子命令式）
| 命令(commands/) | 职责 |
|---|---|
| `register.py` | OTP-only 注册（flag 下沉自原 cli.py；Phase 2 换密码+TOTP） |
| `check_proxy.py` | 探测出口 IP（验证换 sid 换 IP） |
| `stats.py` | 号池统计 |
| `overview.py` | 账号资产总览 |
| `export_accounts.py` | 导出 email----password----2fa[----at] |
| `survival.py` | 批量测活（回写 health_status，每 N 个换 IP） |
| `refresh_at.py` | access_token 续期（过期前跑） |
| `backfill.py` | 补缺失 access_token（密码+TOTP 登录，原子 upsert） |
| `check_imap.py` | 号池 IMAP 可用性检查（决定收码走快通道还是 Graph 降级） |
| `raw_check.py` | 直接喂 JWT 测活（不经 accounts.jsonl） |
| `common.py` | 共享 CLI helper（代理参数/region/RotatingSession/年龄显示） |

**capture/tools/ 未迁移**（保留）：
| 脚本 | 职责 |
|---|---|
| `verify_pwd_totp.py` | 单号注册 CLI 薄壳：选主号 → 生成别名/密码/姓名 → 调 `register_account` → 按 outcome 打印反馈 |
| `batch_totp.py` | 批量：循环调核心（无 subprocess），按失败类型管主号生命周期（不烧号） |
| `login_pwd_check_totp.py` / `check_totp_status.py` | Playwright 交互诊断 |
| `check_survival.py` / `refresh_health.py` | 单号测活 / 刷新+测活二合一（被 survival/refresh 取代，Phase 2 处理） |

### 2. 核心注册链（两条并行路径，共享子步骤）
| 路径 | 文件 | 入口 |
|---|---|---|
| **密码+TOTP（主路线）** | `gptreg/register_pwd.py` | `capture/tools/verify_pwd_totp` / `batch_totp`（Phase 2 迁入 `main.py register`） |
| OTP-only | `gptreg/register_otp.py` | `main.py register` |

**共享子步骤**（处女原则: 消除重复, 不强行合并流程）:
```
auth.signin_flow()      signin 序列(协议节奏内聚) —— 两路径共用
mail/wait_otp.py        wait_otp_with_retry() 收码(代理决策+重发+到件延迟) —— 两路径共用
```

`register_pwd.register_account()` 封装整条链，返回结构化 `RegistrationResult(outcome/diag/record)`（outcome 决定主号"可重试 vs 永久弃用"）。阶段序列：
```
_signin → _register(设密码, 400换sid重试) → _wait_otp(收码, 通道归因)
→ _create(t+so并行) → _session → _enroll_totp(2FA 激活) → 落盘
```

**双路径差异边界**（各自保留, 强行统一反增复杂度）:
| 差异 | register_pwd | register_otp |
|---|---|---|
| 设密码 | 是(quickjs t) | 否 |
| 2FA 激活 | 是(enroll+activate) | 否 |
| 引擎策略 | 固定 quickjs | 动态(pow/quickjs/browser) |
| create 重试 | 单次 | 波次重试+disallow 处理 |

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
| 公共工具 | `mail_util.py` | MailClientError/常量/身份键/UsedCodeCache |
| 共享收码 | `wait_otp.py` | `wait_otp_with_retry`（两注册路径共用） |
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
   ├──测活── main.py survival (定期换IP) → 回写 health_status + last_checked
   ├──续期── main.py refresh (过期前) → 回写 access_token + session_token + expires
   ├──视图── main.py overview (总数/存活/按号源存活率/年龄分布)
   ├──导出── main.py export (email----password----2fa[----at])
   └──号池── MailPool 与账号表联动(反查已注册主号)
```

## 生产循环（完整流程）

```
┌──────────────────────────────────────────────────────────────┐
│  ① 注册           ② 测活          ③ 续期         ④ 导出交付    │
│                                                             │
│  batch_totp        main.py        main.py        main.py     │
│  --pool icloud     survival       refresh        export      │
│  --limit N         (回写health)   (每~6h,        --filter     │
│  --workers 3-5     --source icloud token实测~6h过期)  alive   │
│  (Phase 2 → main.py register)                               │
│     │                                                       │
│     ▼                                                       │
│  10/10成功        10/10存活      账号永活       66个交付      │
└──────────────────────────────────────────────────────────────┘
        ↕ 号池管理: 坏号(已注册)标 bad / 换新号 / 看号源存活率
```

**批量并发流程**（batch_totp --workers N）:
```
号池文件 → MailPool.claim() → ThreadPoolExecutor(N线程)
   每线程: resolve_proxy(新IP) → register_account → 落盘/标号池
   N建议 ≤ 可用IP数; 独立收码号源(iCloud/Outlook)高并发,
   共享收码(CloudMail admin)低并发(≤2)
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

## 架构演进（处女原则）

历次重构目标：命名准确 / 职责单一 / 重复最小 / 组织清晰 / 反馈完整。

| 改动 | 结果 |
|---|---|
| 命名对称 | `register_pwd`/`register_otp`（密码 vs OTP）；`account_store`/`icloud_xdauv`/`mail_util`（名副其实） |
| 职责拆分 | `sentinel.py` 611→442 行，so 头→`sentinel_so.py`，chatReq 观测→`sentinel_chatreq.py` |
| 消除重复 | `auth.signin_flow`（signin 序列）+ `mail/wait_otp`（收码）双路径共享 |
| 依赖内聚 | 协议节奏进 `signin_flow`，调用方无散落 sleep |
| 配置唯一源 | `_DEFAULTS.use_alias=True` 对齐代码兜底，消除语义漂移 |
| 组织分层 | `capture/{tools,legacy,research}/` 三层 |
| 反馈完整 | 8 段精确归因 + 到件延迟 + create 拆解 + so 重试 + 通道/出口归因 |
| 账号闭环 | 注册→测活回写→续期→视图，号源存活率统计 |
