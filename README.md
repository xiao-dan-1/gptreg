# GPT 协议注册机

OTP-only ChatGPT / OpenAI 账号注册工具。  
**默认真 Chrome sentinel（token + so）**——create 用真 t+真 so，唯一实证能存活的路线；Node VM / 纯 Python 为研究用。

参考本地协议分析与既有项目，**独立重写**模块结构，不照搬原仓库。

## 能力

- OTP-only：`signin → authorize → email-otp → create_account → callback → accessToken`
- Sentinel（注册主路径）
  - **默认 `browser`**：真 Chrome `token()`+`sessionObserverToken()` 产真 t+真 so——唯一实证能存活（7h+）
  - **OTP 始终 `pow`**：纯 Python FNV-1a 足矣（`t=""` 被 create 拒，但 OTP 容忍）
  - **研究用 `node`**：Node VM 跑 sdk.js 产非空 t（create 能过，但假 t ~6h 被吊销）；QuickJS 方案产 t=`"0"` 亦非真
  - 假 so（SyntaxError 等）一律丢弃；勿用资料 jsdom/`collector_dx` 当 so 主源
- 登录后最小补齐：`me` + `conversation/init` + `chat-requirements/prepare`（**不**假 finalize）
- 邮箱双源：Outlook MS OAuth / Gmail get-code
- 默认代理与动态辣椒（via chain）
- 批量 / 并发 / 号池状态持久化

## Sentinel 策略（2026-08 更新）

| 项 | 决定 |
|----|------|
| 默认 | `protocol.sentinel_source: browser` |
| **2026-08 存活门槛** | create 只认**非空 `t`**；但假 `t`（SyntaxError 串 / `"0"`）账号 **~6h 被吊销**，真 t+真 so（browser）才能活 7h+ |
| OTP | 始终 `pow`（`t=""` 容忍） |
| 研究用 | `--sentinel-source node`（Node VM 假 t）、QuickJS（t=`"0"`）——create 能过但存活差 |
| 历史 | 7 月曾"create 有/无 so 双活"；8 月收紧 |
| 禁止 | 假 so、假 finalize、资料 jsdom/`collector_dx` 当 so 主源 |

证据与笔记：

```text
capture/create-requires-real-t-20260803.md   （根因 + Node-VM 机制 + 2026-08 实测）
capture/p1-so-survival-20260712/FINDINGS.md  （历史）
```

真 so API 形状（官方 SDK）：

```text
token(flow)                → {p,t,c,...}  永无 so 字段
sessionObserverToken(flow) → {so,c,id,flow}  输入 snapshot_dx（非 collector）
```

## 环境

- Python 3.11+
- **本机 Chrome + Playwright**（默认 `browser` 路线：真 Chrome 产真 t+真 so）
- 可上外网代理（默认 `10808`；辣椒链常用 `7890`）
- Node.js 18+（**仅** `--sentinel-source node` 研究用时需要）

```bash
pip install -r requirements.txt
# browser 可选：
# pip install playwright && playwright install chrome
```

## 配置

1. 复制号池模板并填入邮箱：

```bash
cp mail_pool.txt.example mail_pool.txt
```

2. 按需改 `config.yaml`（代理 / 指纹 / OTP / sentinel）

### Sentinel（`config.yaml` → `protocol`）

```yaml
protocol:
  # browser=默认：真 Chrome 产真 t+真 so（唯一实证能存活）| pow=纯 Python 仅 OTP
  # node=研究用：Node VM 假 t（create 能过但 ~6h 被吊销）
  sentinel_source: "browser"
  sentinel_browser_headless: true
  sentinel_browser_timeout: 60
  # 空=自动 proxy.dynamic.chain_via → proxy.default
  sentinel_browser_proxy: ""
  sentinel_browser_page: "https://auth.openai.com/about-you"
```

### 动态代理（辣椒 lajiao）

```yaml
proxy:
  dynamic:
    enabled: true
    template: "http://账号-region-US-sid-xxxx-t-5:密码@us.lajiaohttp.net:2000"
    region: "US"
    rotate_sid: true
    chain_via: "http://127.0.0.1:7890"
```

说明：

- 改 `region` 换地区；改 `sid` 换 IP（同 sid 在 `t-N` 分钟内粘性）
- 直连辣椒常 403；本机实测 `7890` 可链通，`10808` 不一定
- 单次注册内 sid 固定；不同注册会换 sid

### 登录后（`register.post_login`）

默认 `true`：me + conversation/init + prepare 观测。  
**不解** `chat-requirements/finalize`（无真 pow/turnstile/so 解时禁止伪造）。

## 用法

```bash
# 检测动态代理出口
python main.py --check-proxy
python main.py --check-proxy --check-proxy-times 3
python main.py --check-proxy --region JP

# 号池
python main.py --stats

# 注册 1 个（默认 browser：OTP 纯 pow + create 真 Chrome 产真 t+真 so）
python main.py -n 1

# 批量
python main.py -n 5 -w 2 --continue-on-fail

# 指定代理 / 直连
python main.py -n 1 --proxy socks5h://127.0.0.1:10808
python main.py -n 1 --no-proxy

# 研究用：Node VM 假 t（create 能过但 ~6h 被吊销，勿当默认）
python main.py -n 1 --sentinel-source node

# 纯 Python pow 模式（create 空 t 会被拒，仅排障）
python main.py -n 1 --sentinel-source pow

# 详细日志（含 [Sentinel/chatReq] / post_login prepare 观测）
python main.py -n 1 -v
```

成功账号写入 `output/`：

- `accounts.jsonl`
- `tokens.txt` / `emails.txt` / `full_lines.txt`

## 号池格式

```text
# Outlook
alice@outlook.com----pass----client_id----refresh_token

# Gmail get-code
bob+tag@gmail.com----https://gapi.mailsapi.com/api/get-code?uid=xxx
```

注意：

- 手动 plus 别名请直接写完整地址，程序**不会**二次加别名
- 同一 Gmail `code_url` 批量建议每批 ≤5，避免共享收件箱旧码/限流
- create `registration_disallowed`：优先换根邮箱，勿先改 sentinel

## 目录

```text
main.py
config.yaml
mail_pool.txt.example
gptreg/
  cli.py              命令行
  pipeline.py         注册流水线
  auth.py             协议 + post_login + sentinel 接线
  browser_sentinel.py 真 Chrome token+so（opt-in）
  session.py          curl_cffi 会话
  sentinel.py         纯 Python PoW + chatReq 观测
  otp.py / store.py / mail/
vendor/sentinel/      sdk.js（browser / 研究）
capture/              研究笔记与 retest（含 P1 FINDINGS）
output/               成功账号
data/                 OTP 缓存等
```

## 说明

仅供协议研究与学习。请遵守目标服务条款与当地法律。
