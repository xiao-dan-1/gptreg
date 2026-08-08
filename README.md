# GPT 协议注册机

ChatGPT / OpenAI 账号**密码注册 + TOTP 2FA 激活**工具。纯协议实现，主路线产出带 `totp_secret` 的真激活账号（`mfa_enabled: true`），可后续用密码+TOTP 登录。

## 核心能力

- **密码注册 + TOTP 2FA 激活**（主路线）
  - 核心：`gptreg/register_pwd.py` `register_account()`（结构化结果），CLI/批量共享
  - 薄壳：`capture/tools/verify_pwd_totp.py`（选号/生成参数/打印反馈）
  - `enroll` → `activate_enrollment` 完整链，产出 `mfa_enabled: true` 的真 2FA 账号
  - create 后即时健康检查（秒封检测）
- **批量生产**（`capture/tools/batch_totp.py`）复用核心，按失败类型管主号（IP 风控不烧号）
- **本地 IMAP 收码**（XOAUTH2 经链式隧道），失败自动降级 Graph
- **账号测活 / 补 token / 2FA 登录**（`capture/tools/check_survival.py` / `backfill_token.py` / `login_pwd_check_totp.py`）
- **账号管理闭环**：测活回写（`check_survival_batch`）+ access_token 续期（`refresh_at`）+ 资产视图（`account_overview`）
- **导出交付**（`export_accounts.py`）：`email----password----2fa[----at]` 格式
- 统一落盘 `accounts.jsonl` 主库（去重 upsert + 自动备份）

## 主路线架构

```
号源(Outlook池/iCloud池/CloudMail动态) ──动态链式代理──> OpenAI 注册  [gptreg/register_pwd.register_account]
  ├─ signin → authorize → register(设密码, quickjs_pwd_v3 t)  [400 自动换 sid 重试3次; 落 log-in=已注册弃用]
  ├─ send_otp → 收码(IMAP/Graph/iCloud URL/CloudMail admin) → validate  [iCloud/CloudMail 直连, Outlook 走隧道]
  ├─ create_account(quickjs 真 t + browser 真 so 并行; so 失败重试3次+中止)
  ├─ callback → session(access_token + session_token 刷新凭证) → 即时健康检查(秒封检测)
  ├─ mfa/enroll → activate_enrollment  ← 2FA 真激活(复用注册隧道, 出口贯穿)
  └─ save_account → accounts.jsonl(totp_secret + 凭据, 字段分组顺序)
```

> **默认 plus 别名注册**（config `mail.use_alias: true`）——号池主号很多已在 OpenAI 注册，主号直接注册会落 email-verification/log-in → register 400；别名是全新邮箱，register 直接过（已实证）。`--no-alias` 可强制用主号。**iCloud/CloudMail 一邮箱一账号，强制用主邮箱不别名**（接码 URL/域名绑定主邮箱）。

### Sentinel 策略（主路线）

| 环节 | 引擎 | 说明 |
|---|---|---|
| register(设密码) | quickjs_pwd_v3 | Node VM 跑官方 sdk.js 产真 t |
| create_account | quickjs 真 t + **browser 真 so** | so 走真 Chrome `sessionObserverToken`，跟随注册代理 |
| 登录/OTP | pow | 纯 Python PoW |

> t 一律 **quickjs(Node VM 协议)** 产真值，非浏览器产；`protocol.sentinel_source` 仅影响 OTP-only 流水线(main.py)，主路线 verify_pwd_totp 不读该项。

**硬约束**：禁止假 so（SyntaxError 等）、禁止假 finalize。假 t ~6h 被吊销；真 t+真 so 才能长期存活。

### 收码（插件化: MailSource/MailClient 注册表）

| 通道 | 覆盖 | 速度 | 说明 |
|---|---|---|---|
| 本地 IMAP (XOAUTH2) | 12/15 | ~10s | 经 chain_via 隧道, `_ManualImap` 手动协议, 秒级到件 |
| Graph 降级 | 被拒账号 | ~161s | 索引延迟(服务端), $filter/top/Prefer 优化 + 进度日志 |
| XDAuv 服务 | 全部 | ~8s | `use_xdauv: true`, 服务端直连 Exchange |
| **iCloud 接码 URL** | iCloud | 看服务 | `email----URL` 号源, GET code_url 拉码 |
| **通用 API 插件** | 任意 | 看服务 | `mail.api_client` 配端点即接入, 无需写代码 |
| **CloudMail 号源** | 自托管 | ~7s | `--pool cloudmail` 动态生成邮箱(不依赖号池), admin 拉码 |

- 号池 ~12/15 账号 IMAP 可用；被拒账号（`authenticated but not connected`）自动降级 Graph
- **收码代理策略**：仅 ms_oauth(Outlook IMAP/Graph)走链式隧道；iCloud/CloudMail/API 直连（第三方/自托管服务本身干净, 套隧道反而 TLS 失败）
- **新增收码通道/号源 = 写一个 MailClient/MailSource 插件 + 注册进注册表, 核心零改动**

### 代理

- 动态链式：`proxy.dynamic.template`（cliproxy）经 `chain_via`（7890）隧道，换 sid 换出口 IP
- **住宅 IP 必须**：OpenAI 对数据中心 IP 风控，`register 400 invalid_auth_step` 即 IP 被标记；换干净住宅 IP（AT&T/Comcast/KPN 等）可解
- 当前 cliproxy 池混合住宅/数据中心，换 sid 抽奖——命中住宅 IP 才注册成功

## 环境依赖

- Python 3.11+
- 本机 Chrome + Playwright（browser so 采集需要）
- Node.js 18+（quickjs 引擎产 t 需要）
- 可用外网代理（住宅 IP 出口）

```bash
pip install -r requirements.txt          # curl_cffi + PyYAML
pip install playwright && playwright install chrome
```

## 配置（config.yaml）

```yaml
proxy:
  dynamic:
    enabled: true
    template: "http://账号-region-US-sid-xxxx-t-5:密码@us.cliproxy.io:3010"
    region: "US"
    rotate_sid: false        # true=每次换IP; false=固定sid(粘性)
    chain_via: "http://127.0.0.1:7890"
mail:
  use_xdauv: false           # true=服务收码; false=本地IMAP
  otp_wait: 150              # 收码超时(需覆盖发码延迟)
  otp_max_attempts: 2        # 超时重发次数
  api_client:                # 通用第三方 API 接码(号池行 email----api_key)
    endpoint: ""             # 收码 API URL(空=禁用该来源)
    method: "POST"
    request_body: '{"api_key":"{api_key}","email":"{email}","mailbox":"INBOX"}'
    otp_path: ""             # 响应里 OTP 的 JSON 路径(空=通用扫描)
  cloud_mail:                # 自托管 cloud-mail 号源(号池行单段邮箱, 一邮箱一账号)
    base_url: "https://mail.xdauv.xyz"
    admin_email: ""          # cloud-mail admin(拉码用)
    admin_password: ""
    domains: ["xdauv.xyz"]   # 可用域名(号池行用; 空则查 API)
# 仅 OTP-only 流水线(main.py)用; 主路线 verify_pwd_totp 固定 quickjs 协议产 t, 不读此项
protocol:
  sentinel_source: "browser"
register:
  post_login: true
```

## 使用

### 生产循环（一般流程）

```
① 注册 → ② 测活 → ③ 续期 → ④ 导出交付
   ↕ 号池管理: 坏号(已注册)标 bad / 换新号 / 看号源存活率
```

```bash
# ① 注册（逐个/批量）
python capture/tools/verify_pwd_totp.py --pool icloud --email 用户@icloud.com   # 单个
python capture/tools/batch_totp.py --pool icloud --limit 3                     # 批量
# 号源: 默认 Outlook 池 / --pool icloud / --pool cloudmail(动态生成, 不依赖号池)

# ② 测活(确认账号存活, 回写 health_status)
python capture/tools/check_survival_batch.py
python capture/tools/account_overview.py        # 资产总览(存活/吊销/按号源存活率)

# ③ 续期(access_token 10 天过期前, 账号永活)
python capture/tools/refresh_at.py

# ④ 导出交付
python capture/tools/export_accounts.py                               # email----password----2fa
python capture/tools/export_accounts.py --filter alive --with-at --out deliver.txt  # 存活+at 存文件
```

**注册参数说明**:
```bash
# 默认 plus 别名(解决主号已注册→register 400); --no-alias 用主号
python capture/tools/verify_pwd_totp.py --email 主号
# 指定代理(住宅 IP)
python capture/tools/verify_pwd_totp.py --email 主号 --proxy http://user:pass@host:port
```

成功账号写入 `output/accounts.jsonl`（主库，唯一事实源），字段分组顺序：
`email/mail_main/name/birthdate/mail_type` → `password/totp_secret/access_token/session_token/refresh_token/session_cookies` → `device_id` → `status/health_status/last_checked` → `sentinel_obs` → `proxy_used/saved_at/updated_at`
- `totp_secret` = 2FA 密钥（有值即真 2FA 账号）；`session_token` = 刷新凭证（~3月，access_token 过期续期用）
- 账号管理闭环：测活回写 `health_status` / 续期回写 `session_expires`+`last_refreshed`

### 单号注册输出解读（日志级别前缀 + 完整归因）

```
INFO  [Auth] 获取 providers → CSRF → signin → authorize 落点
INFO  [IMAP] 到件 OTP=123456 uid=.. 延迟 3.0s        ← 收码快通道
INFO  [iCloud] 到件 OTP=305108 延迟 1.9s             ← iCloud 接码 URL
INFO  [MSMail/Graph] 等待中 t+..s                    ← Graph 降级(索引延迟)
  [quickjs/t] register 真 t 就绪 (so: 密码 register 无 so)
  [quickjs/t] create 真 t 就绪 (so 由 browser 采集)
[耗时] signin+register=8.3s OTP段(cloudmail)=3.8s[到件2.5s]
       create段=16.1s session=3.3s health=0.8s enroll=2.3s
       并行(t=1.8s so=11.4s[nav=4.3s sdk=5.3s token=11.16s])=11.4s
[出口] http://region-US-sid-2As2LXe5@us.cliproxy.io:3010   ← 出口 IP(脱敏)
```

- 级别前缀（INFO/WARNING/ERROR）区分正常/降级/失败；t/so 来源明确（quickjs 产 t，browser 采 so）
- 6 段归因精确（各段和 = 总耗时）；**OTP段[到件X.Xs]** 区分段耗时与纯等码延迟；so 内部细分（nav/SDK 加载/token）定位慢点
- 收码 channel 显示号源名（imap/icloud/cloudmail/...）；收码异常带 email + 通道故障 vs 无新邮件归因

## 号池格式（mail_pool.txt）

```text
# ms_oauth（Outlook OAuth, IMAP/Graph 收码）
alice@outlook.com----password----client_id----refresh_token

# icloud（iCloud 邮箱----接码url, GET code_url 拉码; 限定 @icloud.com/@me.com）
user@icloud.com----https://.../code

# api（通用第三方 API, 配 mail.api_client）
user@cloud.com----api_key

# cloudmail（自托管 cloud-mail, 一邮箱一账号; 不依赖号池, --pool cloudmail 动态生成）
```

**来源识别**：4 段 → `ms_oauth` / 2 段+URL(@icloud.com/@me.com) → `icloud` / 2 段非 URL → `api` / 单段邮箱 → `cloudmail`。

**号池文件**：各号源独立文件, CLI 用 `--pool` 选:
- `mail_pool.txt`   Outlook 主号(默认池)
- `icloud_pool.txt` iCloud 接码号(email----URL)
- `--pool cloudmail` 动态生成 cloud-mail 邮箱(不读文件)
**新增来源**：写 `MailSource` 插件 + 注册进 `MAIL_SOURCES`，核心零改动。

- 主号需未注册过 OpenAI（已用会走邮箱级风控，换 IP 无效）
- 注册用 plus 别名（`use_alias: true`），收码用主号 OAuth
- 号池状态（`mail_pool.txt.state.json`）：失败/弃用带 **TTL 自动回退**——基建失败 30min、账号弃用 24h 过期自动恢复（代理/网络恢复账号复活，无需人工清 state）

## 目录

```text
main.py                        OTP-only 流水线入口（非当前主路线）
capture/
  tools/                       运维工具(当前在用)
    verify_pwd_totp.py         主路线：密码注册 + TOTP 2FA 激活
    batch_totp.py              批量生产编排
    check_imap.py              IMAP 可用性检查
    check_survival*.py         账号测活(单/批量, 回写 health_status)
    refresh_at.py              access_token 续期
    account_overview.py        账号资产总览
    export_accounts.py         导出账号(email----password----2fa[----at])
    backfill_token.py          补 access_token
    login_pwd_check_totp.py    密码+TOTP 登录验证
  legacy/                      旧注册路径脚本(verify_* 等, 参考)
  research/                    研究探测脚本(probe_*/t_*_exp/so_* 等)
  reg-2fa-timing-*.md          耗时/性能存档
gptreg/
  register_pwd.py              主路线核心：register_account(注册+TOTP 2FA, 结构化结果)
  auth.py                      协议请求 + sentinel 接线
  register_otp.py              OTP-only 注册(与 register_pwd 对称) + 批量分桶
  browser_sentinel.py          真 Chrome token+so 采集
  sentinel_quickjs.py          Node VM 产真 t
  sentinel_engine.py           引擎注册表
  mail/base.py                 抽象基类(MailClient 收码 / MailSource 来源)
  mail/sources.py              插件注册表(MAIL_SOURCES/MAIL_CLIENTS)
  mail/imap.py                 本地 IMAP XOAUTH2
  mail/ms_graph.py             Graph 兜底
  mail/icloud_xdauv.py         iCloud 接码 URL + XDAuv
  mail/api.py                  通用第三方 API 接码(配置即用)
  mail/cloudmail.py            自托管 cloud-mail(admin 拉码)
  mail/mail_util.py            公共工具(MailClientError/身份键/UsedCodeCache)
  mail/wait_otp.py             共享收码(wait_otp_with_retry, 两注册路径共用)
  mail/pool.py                 号池状态机
  mail/providers.py            build_mail_client 工厂
  proxyutil.py                 动态代理 + 链式隧道
  sentinel_so.py               so 头构造(小PP HAR/内嵌 so 包装)
  sentinel_chatreq.py          chatReq 观测(诊断)
  account_store.py             accounts.jsonl 落盘(主库) + 测活/续期回写
vendor/sentinel/               官方 sdk.js + quickjs 适配器
output/                        成功账号
data/                          OTP 缓存等
```

## 注意事项

- **主号已注册（最常见根因）**：register 400 invalid_auth_step 先看输出诊断行（authorize 落点）——email-verification/log-in = 主号已在 OpenAI 注册，必须用 plus 别名（已默认）；create-account/password = 未注册
- **IP 信誉**：落 create-account/password 仍 400 = 出口 IP 被 OpenAI 标记，需干净住宅 IP；单号注册已内置 register 400 自动换 sid 重试 3 次
- **邮箱级风控**：同一邮箱多次注册失败会被 OpenAI 记住，换 IP 也无效（勿反复试同一邮箱）
- **so 失败中止**：无 so 账号必死（测活实证 2/2 吊销），so 采集失败重试 3 次仍无则中止注册，不白建号
- **主号生命周期（批量）**：SUCCESS 已用 / MAIL_REGISTERED 永久弃用(totp_failed) / IP 风控等可重试不烧号；失败/弃用带 TTL（30min/24h）过期自动回退
- **IMAP 账号级差异**：部分 Outlook 账号被 MS 拒 IMAP（`authenticated but not connected`），自动降级 Graph（较慢）
- **代理通道**：cliproxy 池混合住宅/数据中心，命中住宅 IP 才能注册成功；7890/10808 数据中心 IP 长期风控后不可用

仅供协议研究与学习。请遵守目标服务条款与当地法律。
