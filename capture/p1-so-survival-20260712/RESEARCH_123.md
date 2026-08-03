# 零耗号三项：headed so / 外网对照 / Leslie 归类（2026-07-13）

P1 retest 并行；**不注册、不改默认 pow、不产假 so**。

---

## 1. headed vs headless so 长度

脚本：`capture/browser_so_harvest.py`（已有 `--headed`）

| 批次 | headless | so 字段 len | so_header len | t_len | 备注 |
|------|----------|-------------|---------------|-------|------|
| 20260712-221554 | True | 0 / 0 / 0 | 0 | ~1.3k | 早期 API 路径未通 |
| 20260712-221822 | **True** | **480 / 464** | 2654 / 2702 | ~1.3k | P0 打通 |
| **20260713-002635** | **False (headed)** | **484 / 508** | 2678 / 2702 | ~1.3k | 本轮 2/2 成功 |
| Jennifer（历史） | 真浏览器会话 | **~612** | **~2914** | — | 登录/注册态更丰 |
| P1 A create | browser 注册 | so_header **2718** | （wrapper） | 1412 | 与 harvest wrapper 同量级 |

产物：`capture/browser-so-harvest-20260713-002635/harvest.json`

### 判读

1. **headed 略长于 headless so 字段**（~484–508 vs ~464–480），**未逼近 Jennifer ~612**。  
2. **wrapper（so_header）** headed/headless/P1-A 都在 **~2.6–2.7k**；Jennifer ~2.9k 仍略长。  
3. **有头不是银弹**：同页 `about-you` + `sessionObserverToken` 已足够产真 so；长度差更像会话深度/指纹/行为量，不是「headless 必假」。  
4. P1 A 的 2718 是 **header 整串**，不要和 harvest 的 **so 字段** 直接比成「A 更长所以更真」。

### 策略含义

- 默认 browser harvest / 注册 browser 路径 **继续 headless 即可**（已能 has_so）。  
- 若要逼近 Jennifer 长度：更可能要 **登录态 / 更多行为 / 同源多页**，不是单纯开 headed。  
- 不因 so 字段 50–150 字节差改主路径。

---

## 2. 外网 / 资料：collector vs snapshot

### 2.1 真 SDK（本仓库 vendor + 前序切片）

```text
sessionObserverToken → Nt(so.snapshot_dx)   ★ 主路径
ke / Ut 预热           → collector_dx        （后台，失败可吞）
```

见 `SDK_SESSION_OBSERVER.md`。

### 2.2 本地资料 `资料/chatgpt_register` — **错位**

| 文件 | 行为 |
|------|------|
| `gen_token_jsdom.js:107-109` | `Nt(chatReq.so.collector_dx)` ← **错字段** |
| `sentinel_token.py` `get_so_token` | `collector_dx` → `run_session_observer_vm*` |
| 注释 | 知道有 `sessionObserverToken`，实现却没走公开 API + snapshot |

→ jsdom 即使跑出字符串，也**不是**浏览器 `sessionObserverToken` 语义。  
**禁止并入主路径**（与 REF 一致）。

### 2.3 GitHub `leetanshaj/openai-sentinel`

- 只有 **PoW + 组装** `{p, t, c}`  
- `t` 甚至直接塞 **`turnstile.dx` 原文**（不跑 Pn VM）  
- **零** `sessionObserver` / `snapshot_dx` / `collector_dx` / so-header  
- 目标 flow 示例：`sora_create_task`（chat 侧），不是注册 create 双头

→ 纯 token 路线；**不产 so**。不能当「真 so 参考」。

### 2.4 GitHub `realasfngl/ChatGPT`

- `wrapper/chatgpt.py`：`chat-requirements` +  
  `openai-sentinel-chat-requirements-token` / `proof-token` / `turnstile-token`  
- **无** `openai-sentinel-so-token`  
- reverse 树是 challenge/VM 反编译，**无** snapshot/collector 字符串命中（本轮 raw 扫）

→ 偏 **chat 旧/另一套** 头，不是注册 create 的 so 双头。

### 2.5 对照表

| 来源 | 产 so？ | 用哪路 dx | 可并主路径？ |
|------|---------|-----------|--------------|
| 真 Chrome `sessionObserverToken` | 是 | **snapshot_dx** | 已是 P0.5 opt-in |
| 资料 jsdom / get_so | 试图是 | **collector_dx（错）** | **否** |
| leetanshaj | 否 | 无 | 仅 pow 参考 |
| realasfngl | 否（chat 头） | 无 so | 否（场景不同） |

### 2.6 结论

外网常见实现 **要么无 so，要么（本地资料）误用 collector**。  
我们「token → sessionObserverToken + snapshot」与官方 SDK 对齐；**不要向资料/leet 收敛**。

---

## 3. Leslie `registration_disallowed` 归类

### 3.1 事实链（`run_B_pow.log`）

| 步 | Leslie | Eric（同 log 第二次） |
|----|--------|------------------------|
| mode | pow | pow |
| has_so create | **false** | **false** |
| OTP | 通过 → about_you | 通过 → about_you |
| about-you | 200 | 200 |
| create | **400** `registration_disallowed` | **200** + 长活 |
| 时间 | 22:44:57 | 22:45:38（约 1min 后） |
| proxy | 辣椒 US via-chain | 同类型 via-chain（不同 sid） |
| birthdate | 2005-04-08 | 2000-04-21 |
| name | 随机 | 随机 |

原始错误：

```text
code: registration_disallowed
message: Sorry, we cannot create your account with the given information.
type: invalid_request_error
```

endpoint：`auth.openai.com/api/accounts/create_account`（协议 create，非 health）。

### 3.2 能排除 / 不能排除

| 假说 | 判定 | 理由 |
|------|------|------|
| 「无 so → create 必 disallow」 | **排除（本对照）** | Eric 同无 so create 200 |
| 协议步缺失（OTP/about-you） | **排除** | Leslie 全绿到 create |
| 纯代理类型（辣椒 US） | **弱** | Eric 同类型成功；非充分 |
| **邮箱根 / 身份风控** | **最可能** | 唯一硬差是 main 邮箱根 + 随机 name/bd |
| birthdate/name 随机命中规则 | 可能 | 单次无法拆 |
| 号池脏/曾用 | 待查 | state 显示 Leslie used=1（本失败计入） |

### 3.3 归类标签

```text
registration_disallowed @ create_account
  class: identity_or_mailbox_risk   # 优先
  not:   missing_so                 # 同条件 Eric 反例
  not:   protocol_step_skip         # OTP+about-you 已过
  action: 换根邮箱重试（已做 → Eric）
  action_not: 为 Leslie 单独开 so / 改 sentinel
```

### 3.4 运维含义

- create 400 + `registration_disallowed` → **先换号/换根**，不要先改 sentinel。  
- 与 delayed ban（token_revoked）是不同层：前者 **建号被拒**，后者 **建号后探活死**。  
- P1 B 失败 1 次属预期噪声；对照有效性靠 Eric 成功维持。

---

## 与 P1 的关系

| 项 | 对 so 因果 |
|----|------------|
| headed 长度 | 不改变「是否交 so」；P1 A/B 变量仍是有无 so-header |
| 外网错 collector | 强化 **勿并资料**；我们路径正确 |
| Leslie | **勿把 create 400 算进 so 失败** |

P1 仍以 retest 120 定存活差。

## 产物路径

- headed harvest: `capture/browser-so-harvest-20260713-002635/`  
- 本笔记: `capture/p1-so-survival-20260712/RESEARCH_123.md`  
- 前序: `SDK_SESSION_OBSERVER.md` / `REF_chatgpt_register.md` / `FINDINGS.md`
