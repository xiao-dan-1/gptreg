# ✨ GPT 协议注册

> **纯协议实现**的 ChatGPT / OpenAI 账号自动注册工具——产出带 `totp_secret` 的**真 2FA 账号**（`mfa_enabled: true`），可用密码 + TOTP 正常登录。

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)
![Mode](https://img.shields.io/badge/纯协议注册-零浏览器交互-2ea44f)

> ⚠️ **运行前置**：本项目只提供注册/收码管线，**不包含任何邮箱号池、代理或接码服务**——需自备住宅代理 + 邮箱号池（见 [号池](#📮-号池自备邮箱)）。仅供协议研究与学习，请遵守目标服务条款与当地法律。

---

## 🚀 快速开始（5 分钟跑通第一个账号）

> 目标：装好环境 → 填号池 → 注册出第一个带 2FA 的账号。技术细节可先跳过（见文末 [🧠 进阶](#🧠-进阶架构与协议可跳读)）。

### 1️⃣ 准备环境

- **Python 3.11+**
- **Chrome**（so 采集用）+ **Node.js 18+**（token 生成用）
- **一个能上外网的住宅代理**（数据中心 IP 会被 OpenAI 风控，注册必失败）

### 2️⃣ 安装依赖

```bash
pip install -r requirements.txt
pip install playwright && playwright install chrome
```

### 3️⃣ 配置代理（`config.yaml`）

复制 `config.yaml.example` 为 `config.yaml`，填入你的代理：

```yaml
proxy:
  dynamic:
    enabled: true
    template: "http://你的账号-region-US-sid-xxxx-t-5:你的密码@us.cliproxy.io:3010"
    chain_via: "http://127.0.0.1:7890"   # 你本机代理的第一跳
```

### 4️⃣ 填号池（`icloud_pool.txt`，首选号源）

准备你的邮箱号池（见 [号池](#号池自备邮箱)）。**iCloud 号池**（`邮箱----接码URL`）示例：

```text
user@icloud.com----https://你的接码服务/get-code
```

> 用 Outlook 池则填 `mail_pool.txt`（`email----password----client_id----refresh_token`），并把第 5 步命令去掉 `--pool icloud`。

### 5️⃣ 注册第一个账号

```bash
python capture/tools/batch_totp.py --pool icloud --limit 1
# 成功 → output/accounts.jsonl 新增一条带 TOTP 的账号
```

### 6️⃣ 查看结果

```bash
python main.py overview     # 账号总览(总数/存活/按号源存活率)
python main.py export       # 导出 email----password----2fa 交付
```

> **首选 iCloud 号源**（实测存活率 ~100%）：`icloud_pool.txt` 填 `邮箱----接码URL`。

---

## 🔁 日常使用（生产循环）

> 生产循环：**① 注册 → ② 测活 → ③ 续期 → ④ 导出交付**（坏号标 bad / 换新号）

```bash
# ① 注册：批量注册，--workers 并发线程（建议 ≤ 可用代理数）
python capture/tools/batch_totp.py --pool icloud --limit N --workers M

# ② 测活：批量测活，回写 health_status，每 8 个换出口 IP
python main.py survival --source icloud

# ③ 续期：access_token 续期（实测 ~6h 过期，见 FAQ）
python main.py refresh

# ④ 导出：导出存活账号，四段格式含 access_token
python main.py export --filter alive --with-at
```

**其他工具**：

```bash
python main.py overview                  # 资产总览（总数/存活/按号源存活率）
python main.py stats                     # 号池统计
python main.py check-proxy --times 2     # 探测出口 IP（换 sid 验证换 IP）
python main.py backfill --emails xxx     # 补缺失的 access_token
python main.py imap --limit 3            # 号池 IMAP 可用性检查
python main.py subscription              # 查订阅 / 优惠资格
echo "<jwt>" | python main.py raw-check  # 直接喂 JWT 测活
```

---

## ⚙️ 配置说明（`config.yaml` 关键项）

| 配置 | 作用 |
|---|---|
| `proxy.dynamic.template` | 动态住宅代理模板（换 sid = 换出口 IP） |
| `proxy.dynamic.chain_via` | 本地代理第一跳（如 127.0.0.1:7890） |
| `mail.use_alias` | 用 plus 别名注册（解决主号已注册的 400），默认 true |
| `register.default_password` | **统一密码**（推荐填）：所有账号同一密码，半注册邮箱可找回；不填则随机（密码会随进程丢失） |
| `mail.otp_wait` | 收码超时（秒），需覆盖发码延迟 |

完整配置示例见 `config.yaml.example`。

---

## 📮 号池（自备邮箱）

本项目**不提供邮箱**，需自备。号池按行组织，行首 `#` 为注释。

| 号源 | 行格式 | 邮箱来源 |
|---|---|---|
| **ms_oauth**（Outlook 池，默认） | `email----password----client_id----refresh_token` | 自建 Outlook 邮箱 + OAuth 授权拿 refresh_token |
| **iCloud**（推荐） | `email----https://.../get-code` | 自备 `@icloud.com/@me.com` 邮箱 + 第三方接码服务 |
| **api** | `email----api_key` | 第三方接码平台（配 `mail.api_client`） |
| **cloudmail** | 动态生成 | 自托管 cloud-mail 服务（`--pool cloudmail`，不依赖号池文件） |

- **来源识别**：4 段 → `ms_oauth`；2 段+URL(@icloud.com/@me.com) → `icloud`；2 段非 URL → `api`；单段邮箱 → `cloudmail`
- **号池状态**（`.state.json`）：失败/弃用带 TTL 自动回退（基建 30min、弃用 24h），代理恢复即复活
- 主号需**未注册过 OpenAI**（已用会走邮箱级风控，换 IP 无效）

---

## 📦 账号输出与交付

成功账号写入 `output/accounts.jsonl`（主库，唯一事实源）。字段分组：
`email/name/birthdate/mail_type` → `password/totp_secret/access_token/session_token/session_cookies` → `device_id` → `status/health_status` → `sentinel_obs` → `proxy_used/saved_at/updated_at`

- `totp_secret` = 2FA 密钥（有值 = 真 2FA 账号）
- `session_token` = 刷新凭证（~3 月，token 过期续期用）

**导出格式**：`email----password----2fa[----at]`（`--with-at` 加第 4 段 access_token）。

---

## ❓ 常见问题（FAQ）

**Q1. 🤔 注册报 `register 400`，为什么？**
看输出的诊断行（authorize 落点）三选一：
- `email-verification/log-in` → 主号已在 OpenAI 注册 → 用 plus 别名（已默认）或换邮箱
- `create-account/password` 仍 400 → **出口 IP 被标记** → 换干净住宅 IP
- `invalid_auth_step` / `Invalid authorization` → **邮箱已推进过注册流程**（状态机不可重入）→ 弃用，换新邮箱

**Q2. 🕒 测活报 `token_expired`，账号死了吗？**
没死。**access_token 实测 ~6h 过期**（非 README 说的 10 天），账号还在，只是 token 需续期。`main.py refresh` 续期——**但注意**：实测部分账号 refresh 返回"同 token"（未换新），续期是否生效以续期后重测为准。

**Q3. 🌐 每次注册都换 IP，为什么还会风控？**
风控是**多维**的：换 IP 只解决"IP 信誉"一维。**邮箱级**（同邮箱多次失败被记住）、**频率级**（`rate_limit_exceeded`）、**会话级**均换 IP 无效。今天注册量过大也会触发认证频率限流。

**Q4. 🔑 半注册邮箱（register 成功但 create/so 失败）怎么找回？**
用**统一密码**（`register.default_password`）手动登录——这就是统一密码的意义。不填统一密码则随机密码已随进程丢失，无法找回。

**Q5. 📬 收码失败/很慢？**
- iCloud/CloudMail/API 走**直连**；Outlook 走链式隧道
- 被 MS 拒 IMAP 的 Outlook 账号自动降级 Graph（较慢 ~161s）
- 共享收码源（CloudMail admin）并发 ≤2，独立收码（iCloud URL/Outlook IMAP）可高并发

---

## 🧠 进阶：架构与协议（可跳读）

### 主路线注册链

```
号源(Outlook/iCloud/CloudMail) ──动态链式代理──> OpenAI 注册
  ├─ signin → authorize → register(设密码)   [400: 状态冲突弃用 / IP 类换 sid 重试 1 次]
  ├─ send_otp → 收码 → validate
  ├─ create_account(quickjs 真 t + browser 真 so 并行)
  ├─ callback → session(access_token + session_token) → 健康检查(秒封检测)
  ├─ mfa/enroll → activate_enrollment  ← 2FA 真激活
  └─ save_account → accounts.jsonl
```

### Sentinel 策略（产真 t + 真 so）

| 环节 | 引擎 | 说明 |
|---|---|---|
| register(设密码) | quickjs_pwd_v3 | Node VM 跑官方 sdk.js 产真 t |
| create_account | quickjs 真 t + **browser 真 so** | so 走真 Chrome `sessionObserverToken` |
| 登录/OTP | pow | 纯 Python PoW |

**硬约束**：禁止假 so / 假 finalize。假 t ~6h 被吊销；真 t + 真 so 才能长期存活。

### 收码通道（插件化）

| 通道 | 速度 | 说明 |
|---|---|---|
| 本地 IMAP (XOAUTH2) | ~10s | Outlook，经链式隧道 |
| Graph 降级 | ~161s | 被 MS 拒 IMAP 的账号兜底 |
| iCloud 接码 URL | 看服务 | `email----URL` 号源 |
| CloudMail | ~7s | 自托管，admin 拉码 |
| 通用 API | 看服务 | `mail.api_client` 配端点即接入 |

### 代理

- 动态链式：cliproxy 模板经 `chain_via` 隧道，换 sid 换出口 IP
- **住宅 IP 必须**（数据中心 IP 被 OpenAI 风控）
- cliproxy 池混合住宅/数据中心，换 sid 是"抽奖"——命中住宅 IP 才成功

---

## 📁 目录结构

```text
main.py                        统一 CLI 入口(子命令式)
gptreg/
  cli.py                       CLI 路由器
  commands/                    子命令实现(register/check-proxy/stats/overview/export/
                                survival/refresh/backfill/imap/raw-check/subscription)
  register_pwd.py              主路线核心: register_account(注册+TOTP 2FA)
  register_otp.py              OTP-only 并行路径
  auth.py                      协议请求
  sentinel*.py                 Sentinel 引擎(quickjs 真 t / browser 真 so / pow)
  mail/                        收码插件体系(IMAP/Graph/iCloud/CloudMail/API + 号池状态机)
  proxyutil.py                 动态代理 + 链式隧道
  account_store.py             accounts.jsonl 落盘 + 测活/续期回写
  health.py / session.py / jwtutil.py / postlogin.py
capture/
  tools/                       主路线 CLI(verify_pwd_totp 单号 / batch_totp 批量)
  legacy/  research/           旧路径参考 + 研究探测脚本
vendor/sentinel/               官方 sdk.js + quickjs 适配器
output/                        成功账号(accounts.jsonl)
```

---

## ⚠️ 注意事项

- **主号已注册（最常见根因）**：register 400 看 authorize 落点——`email-verification/log-in` = 主号已注册，用 plus 别名（已默认）；`create-account/password` = 未注册
- **IP 信誉**：落 create-account/password 仍 400 = IP 被 OpenAI 标记，需干净住宅 IP；纯 IP 类 400 内置换 sid 重试 1 次（不反复戳同邮箱）
- **邮箱状态冲突（不可重入）**：`invalid_auth_step`/`Invalid authorization` = 邮箱已推进注册（OTP 已消费/密码已设），重跑必 400，换 IP 无效——批量下自动弃用
- **已推进邮箱不可重跑**：register/OTP 之后的失败（so/create/session/enroll）= 已推进，批量下弃用
- **邮箱级风控**：同一邮箱多次失败会被 OpenAI 记住，换 IP 无效（勿反复试）
- **so 失败中止**：无 so 账号必死（实测 2/2 吊销），so 采集失败重试 3 次仍无则中止，不白建号
- **access_token ~6h 过期**（实测，非 10 天）：测活 `token_expired` = 过期可续期（独立状态），续期机制见 FAQ
- **统一密码（推荐）**：`register.default_password` 填统一密码，半注册邮箱可找回；不填则随机密码随进程丢失
- **代理通道**：cliproxy 池混合住宅/数据中心，命中住宅 IP 才注册成功

仅供协议研究与学习。请遵守目标服务条款与当地法律。
