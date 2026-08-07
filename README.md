# GPT 协议注册机

ChatGPT / OpenAI 账号**密码注册 + TOTP 2FA 激活**工具。纯协议实现，主路线产出带 `totp_secret` 的真激活账号（`mfa_enabled: true`），可后续用密码+TOTP 登录。

## 核心能力

- **密码注册 + TOTP 2FA 激活**（主路线，`capture/verify_pwd_totp.py`）
  - `enroll` → `activate_enrollment` 完整链，产出 `mfa_enabled: true` 的真 2FA 账号
- **批量生产**（`capture/batch_totp.py`）自动选未用过主号逐个注册
- **本地 IMAP 收码**（XOAUTH2 经链式隧道），失败自动降级 Graph
- **账号测活 / 补 token / 2FA 登录**（`capture/check_survival.py` / `backfill_token.py` / `login_pwd_check_totp.py`）
- 统一落盘 `accounts.jsonl` 主库（去重 upsert）

## 主路线架构

```
主号(号池) ──动态链式代理──> OpenAI 注册
  ├─ signin → authorize → register(设密码, quickjs_pwd_v3 t)
  ├─ send_otp → 本地 IMAP 收码 → validate
  ├─ create_account(quickjs 真 t + browser 真 so)
  ├─ callback → session(access_token)
  ├─ mfa/enroll → activate_enrollment  ← 2FA 真激活
  └─ save_account → accounts.jsonl(totp_secret + 凭据)
```

### Sentinel 策略（主路线）

| 环节 | 引擎 | 说明 |
|---|---|---|
| register(设密码) | quickjs_pwd_v3 | Node VM 跑官方 sdk.js 产真 t |
| create_account | quickjs 真 t + **browser 真 so** | so 走真 Chrome `sessionObserverToken`，跟随注册代理 |
| 登录/OTP | pow | 纯 Python PoW |

> t 一律 **quickjs(Node VM 协议)** 产真值，非浏览器产；`protocol.sentinel_source` 仅影响 OTP-only 流水线(main.py)，主路线 verify_pwd_totp 不读该项。

**硬约束**：禁止假 so（SyntaxError 等）、禁止假 finalize。假 t ~6h 被吊销；真 t+真 so 才能长期存活。

### 收码

- **本地 IMAP**（默认，`use_xdauv: false`）：`IMAPOAuthClient`，XOAUTH2 经 `chain_via` 隧道，`_ManualImap` 手动协议，秒级到件
- **Graph 降级**：IMAP 不可用（MS 账号级拒绝）时自动降级，`$filter` 时间窗口 + `$top` + `Prefer text` 优化，有索引进度日志
- 号池 ~12/15 账号 IMAP 可用；被拒账号（`authenticated but not connected`）自动降级

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
# 仅 OTP-only 流水线(main.py)用; 主路线 verify_pwd_totp 固定 quickjs 协议产 t, 不读此项
protocol:
  sentinel_source: "browser"
register:
  post_login: true
```

## 使用

```bash
# 注册一个 2FA 账号（主路线，动态链式代理）
python capture/verify_pwd_totp.py --email 主号

# 指定代理（住宅 IP）
python capture/verify_pwd_totp.py --email 主号 --proxy http://user:pass@host:port

# 批量生产 3 个
python capture/batch_totp.py --limit 3

# 固定代理批量
python capture/batch_totp.py --limit 3 --no-dynamic --proxy http://127.0.0.1:7890

# 检查号池 IMAP 可用性（决定走快通道还是 Graph 降级）
python capture/check_imap.py --limit 20

# 测活账号
python capture/check_survival.py

# 补缺失 access_token 的账号 token（密码+TOTP 登录）
python capture/backfill_token.py
```

成功账号写入 `output/accounts.jsonl`（主库）：
`email/password/access_token/refresh_token/totp_secret/session_cookies/proxy_used/status/updated_at`

## 号池格式（mail_pool.txt）

```text
# Outlook（ms_oauth，OAuth 凭据收码）
alice@outlook.com----password----client_id----refresh_token
```

- 主号需未注册过 OpenAI（已用会走邮箱级风控，换 IP 无效）
- 注册用 plus 别名（`use_alias: true`），收码用主号 OAuth

## 目录

```text
main.py                        OTP-only 流水线入口（非当前主路线）
capture/
  verify_pwd_totp.py           主路线：密码注册 + TOTP 2FA 激活
  batch_totp.py                批量生产编排
  check_imap.py                IMAP 可用性检查
  check_survival.py            账号测活
  backfill_token.py            补 access_token
  login_pwd_check_totp.py      密码+TOTP 登录验证
  reg-2fa-timing-*.md          耗时/性能存档
gptreg/
  auth.py                      协议请求 + sentinel 接线
  pipeline.py                  OTP-only 流水线 + 批量分桶
  browser_sentinel.py          真 Chrome token+so 采集
  sentinel_quickjs.py          Node VM 产真 t
  sentinel_engine.py           引擎注册表
  mail/providers.py            收码（IMAP/Graph/XDAuv/Gmail）
  mail/pool.py                 号池状态机
  proxyutil.py                 动态代理 + 链式隧道
  store.py                     accounts.jsonl 落盘
vendor/sentinel/               官方 sdk.js + quickjs 适配器
output/                        成功账号
data/                          OTP 缓存等
```

## 注意事项

- **IP 风控**：`register 400 invalid_auth_step` = 出口 IP 被 OpenAI 标记，需换干净住宅 IP，不是代码 bug
- **邮箱级风控**：同一主号多次注册失败会被 OpenAI 记住，换 IP 也无效（勿反复试同一主号）
- **IMAP 账号级差异**：部分 Outlook 账号被 MS 拒 IMAP（`authenticated but not connected`），自动降级 Graph（较慢）
- **代理通道**：cliproxy 池混合住宅/数据中心，命中住宅 IP 才能注册成功；7890/10808 数据中心 IP 长期风控后不可用

仅供协议研究与学习。请遵守目标服务条款与当地法律。
