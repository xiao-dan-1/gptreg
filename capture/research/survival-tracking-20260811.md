# 存活追踪：fast/frame 组合 vs 标准 so（2026-08-11 起）

> 目标：验证 `sentinel_browser_fast` + `sentinel_so_page(frame.html)` 组合产出的 so
> 是否与标准 so 一样长活。若样本(fast+frame)数小时后仍 ok → fast 可转默认。
> 定时测活(每 33 分钟)追加记录；对照 = 标准 so 注册的 4 个号。

## 样本说明

- **[FAST+FRAME]** KirstenScott5455+16w46n@outlook.com — 2026-08-11 19:33 注册(fast+frame+池)
- 对照(标准 so, 2026-08-11 ~18:5x 注册): JasonCopeland6778 / JoseWhitney3017 / RickyTaylor4773 / AdamAdams2659

## 基线(2026-08-11 ~19:45)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 0.7h | ok | 200 | 对照 |
| JoseWhitney3017 | 0.7h | ok | 200 | 对照 |
| RickyTaylor4773 | 0.7h | ok | 200 | 对照 |
| AdamAdams2659 | 0.7h | ok | 200 | 对照 |
| **KirstenScott5455** | **0.0h** | **ok** | **200** | **[FAST+FRAME]** |

## 追踪记录(每次定时追加)

### 2026-08-11 ~20:00(手动)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 0.8h | ok | 200 | 对照 |
| JoseWhitney3017 | 0.8h | ok | 200 | 对照 |
| RickyTaylor4773 | 0.8h | ok | 200 | 对照 |
| AdamAdams2659 | 0.8h | ok | 200 | 对照 |
| **KirstenScott5455** | **0.1h** | **ok** | **200** | **[FAST+FRAME]** |

### 2026-08-11 ~20:35(定时 1)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 1.47h | ok | 200 | 对照 |
| JoseWhitney3017 | 1.46h | ok | 200 | 对照 |
| RickyTaylor4773 | 1.46h | ok | 200 | 对照 |
| AdamAdams2659 | 1.45h | ok | 200 | 对照 |
| **KirstenScott5455** | **0.77h** | **ok** | **200** | **[FAST+FRAME]** |

> 观察：对照 4 号 1.5h 仍全活；KirstenScott 0.77h ok。未到 3h 判定点。

### 2026-08-11 ~21:15(定时 2)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 1.89h | ok | 200 | 对照 |
| JoseWhitney3017 | 1.88h | ok | 200 | 对照 |
| RickyTaylor4773 | 1.88h | ok | 200 | 对照 |
| AdamAdams2659 | 1.87h | ok | 200 | 对照 |
| **KirstenScott5455** | **1.18h** | **ok** | **200** | **[FAST+FRAME]** |

> 观察：对照 4 号 1.9h 全活；KirstenScott 1.18h ok。未到 3h 判定点。

### 2026-08-11 ~21:40(定时 3)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 1.93h | ok | 200 | 对照 |
| JoseWhitney3017 | 1.92h | ok | 200 | 对照 |
| RickyTaylor4773 | 1.92h | ok | 200 | 对照 |
| AdamAdams2659 | 1.91h | ok | 200 | 对照 |
| **KirstenScott5455** | **1.23h** | **ok** | **200** | **[FAST+FRAME]** |

> 观察：对照 4 号 ~1.9h 全活；KirstenScott 1.23h ok。未到 3h 判定点。

### 2026-08-11 ~21:10(手动)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 2.32h | ok | 200 | 对照 |
| JoseWhitney3017 | 2.31h | ok | 200 | 对照 |
| RickyTaylor4773 | 2.30h | ok | 200 | 对照 |
| AdamAdams2659 | 2.29h | ok | 200 | 对照 |
| **KirstenScott5455** | **1.61h** | **ok** | **200** | **[FAST+FRAME]** |

> 观察：对照 4 号 2.3h 全活；KirstenScott 1.61h ok。接近 3h 判定点。

### 2026-08-11 ~21:40(定时 4)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 2.46h | ok | 200 | 对照 |
| JoseWhitney3017 | 2.45h | ok | 200 | 对照 |
| RickyTaylor4773 | 2.45h | ok | 200 | 对照 |
| AdamAdams2659 | 2.44h | ok | 200 | 对照 |
| **KirstenScott5455** | **1.76h** | **ok** | **200** | **[FAST+FRAME]** |

> 观察：对照 4 号 2.45h 全活(接近 3h)；KirstenScott 1.76h ok。

### 2026-08-11 ~22:10(定时 5)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 2.91h | ok | 200 | 对照 |
| JoseWhitney3017 | 2.90h | ok | 200 | 对照 |
| RickyTaylor4773 | 2.90h | ok | 200 | 对照 |
| AdamAdams2659 | 2.89h | ok | 200 | 对照 |
| **KirstenScott5455** | **2.20h** | **ok** | **200** | **[FAST+FRAME]** |

> **关键**：对照 4 号 2.9h 全活(马上到 3h)；KirstenScott 2.20h ok(还需 ~0.8h)。
> 下一轮(约 22:40)对照号预计过 3h——若仍 ok 即可初步确认标准 so 长活。

### 纯协议(vm so)账号存活对比(2026-08-11 ~22:06)

**AlbertHall3419+7f0d71**(纯协议注册,quickjs vm so,so_len=2682):
- 注册: 2026-08-11 22:05:01,44.3s,产 password+2fa(TOTP 激活成功)
- 注册后 health=ok(日志)
- **立即测活: status=invalidated http=401(code=token_invalidated)**
- 对比: browser so 账号 2.9h+ 仍 ok → **vm so 秒死(注册即吊销)**

> **结论**: 当前环境 vm so 账号注册后立即吊销, 比 survey 旧记录(7-30min)更糟。

### 2026-08-11 ~23:00(定时 6)——判定点达成

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 3.72h | ok | 200 | 对照 |
| JoseWhitney3017 | 3.71h | ok | 200 | 对照 |
| RickyTaylor4773 | 3.71h | ok | 200 | 对照 |
| AdamAdams2659 | 3.70h | ok | 200 | 对照 |
| **KirstenScott5455** | **3.02h** | **ok** | **200** | **[FAST+FRAME]** |

> **🎯 判定点达成**：
> 1. 对照 4 号(标准 so)3.7h 全活 → 标准 so 长活确认
> 2. **KirstenScott(fast+frame)3.02h ok, 超过 3h 判定点** → fast+frame 提速不伤存活
> **结论：`sentinel_browser_fast` 可转默认**（so 采集 8s→4.5s, 存活不降）。

> 纯协议(quickjs)路线账号不可存活——browser 真 so 是唯一长活路径(再次实证)。

### AlbertHall 重登验证(2026-08-11 ~23:05)——确认封号

用 `test_login_2fa.py`(password+TOTP 完整登录)验证:
- 密码验证返回 **403: "You do not have an account because it has been deleted or deactivated"**
- **= `account_deactivated`(账号被删除/停用), 非 token 失效**

> **结论**: vm so 账号**建号即封**(账号被 OpenAI 停用, 密码验证 403)。
> 不是社区说的"token_invalidated 可恢复"(那对 browser so 正常账号成立);
> vm so 账号是账号级停用, 无法重登恢复。**纯协议(vm so)路线账号彻底不可用**。

### 协议注册存活实验(2026-08-11 ~22:50)OTP-only vs 补密码+TOTP

**受控实验**: SabrinaWells1452+3c10ec(vm so, OTP-only, 不补密码不开TOTP)
- 注册: 22:47:26, 68.6s, vm so(2722), post_login 全 ok(me/conversation_init/prepare 200)
- 注册 IP: 1024proxy sid-GMJzntL0
- **立即同 IP 测活: ok(200)**
- 对比 AlbertHall(补密码+TOTP): 注册后秒死(403 deactivated), 且当时 warmup me=401

> **假设**: 补密码+TOTP 流程或跨 IP 测活是杀号主因; OTP-only + 同 IP 可能存活。
> 需持续同 IP 追踪确认(目标 >20min)。
- 22:48:18 同IP sid=GMJzntL0 -> ok http=200
- 22:52:22 同IP sid=GMJzntL0 -> ok http=200
- 22:53:16 同IP sid=GMJzntL0 -> ok http=200
- 22:58:10 同IP sid=GMJzntL0 -> error http=None
- 22:58:19 同IP sid=GMJzntL0 -> ok http=200
- 23:01:34 同IP sid=GMJzntL0 -> ok http=200
- 23:13:00 同IP sid=GMJzntL0 -> error http=None
- 23:13:12 同IP sid=GMJzntL0 -> invalidated http=401

### SabrinaWells 死亡(2026-08-11 ~23:13)——OTP-only 活 26min 仍死

| 时间 | age | 状态 |
|---|---|---|
| 22:48 | 1min | ok |
| 22:52 | 5min | ok |
| 22:58 | 11min | ok |
| 23:01 | 14min | ok |
| 23:13 | 26min | **invalidated(401)** |

> **结论修正**：OTP-only 账号活 ~26min 后死(比补密码+TOTP 秒死长, 但不长活)。
> 与 BarbaraNolan(08-10, 21-31min)一致 → **vm so 账号无论是否补密码最终必死**。
> 补密码+TOTP 让死亡提前(秒死 vs 26min)；OTP-only 只是延缓。
> **最终结论**：协议注册(vm so)最长活 ~26min 必死；browser 真 so 是唯一长活路径。

### 2026-08-11 ~23:30(定时 7)——browser so 长活确认

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 4.39h | ok | 200 | 对照 |
| JoseWhitney3017 | 4.38h | ok | 200 | 对照 |
| RickyTaylor4773 | 4.38h | ok | 200 | 对照 |
| AdamAdams2659 | 4.37h | ok | 200 | 对照 |
| **KirstenScott5455** | **3.68h** | **ok** | **200** | **[FAST+FRAME]** |

> browser so 账号 4.4h 全活, fast+frame 样本 3.68h ok——长活确认。
> (对照 vm so 账号: 秒死~26min 必死)

### 2026-08-11 ~23:50(定时 8)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 4.89h | ok | 200 | 对照 |
| JoseWhitney3017 | 4.88h | ok | 200 | 对照 |
| RickyTaylor4773 | 4.88h | ok | 200 | 对照 |
| AdamAdams2659 | 4.87h | ok | 200 | 对照 |
| **KirstenScott5455** | **4.18h** | **ok** | **200** | **[FAST+FRAME]** |

> browser so 账号近 5h 全活;fast+frame 样本 4.18h ok——长活持续确认。

### 2026-08-12 ~00:10(定时 9)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 5.38h | ok | 200 | 对照 |
| JoseWhitney3017 | 5.37h | ok | 200 | 对照 |
| RickyTaylor4773 | 5.37h | ok | 200 | 对照 |
| AdamAdams2659 | 5.36h | ok | 200 | 对照 |
| **KirstenScott5455** | **4.68h** | **ok** | **200** | **[FAST+FRAME]** |

> browser so 账号 5.4h 全活; fast+frame 样本 4.68h ok——长活持续确认。

### 2026-08-12 ~00:45(定时 10)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 5.89h | ok | 200 | 对照 |
| JoseWhitney3017 | 5.88h | ok | 200 | 对照 |
| RickyTaylor4773 | 5.88h | ok | 200 | 对照 |
| AdamAdams2659 | 5.87h | ok | 200 | 对照 |
| **KirstenScott5455** | **5.18h** | **ok** | **200** | **[FAST+FRAME]** |

> browser so 账号近 6h 全活; fast+frame 样本 5.18h ok——长活持续确认。

### 2026-08-12 ~01:15(定时 11)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 6.38h | ok | 200 | 对照 |
| JoseWhitney3017 | 6.37h | ok | 200 | 对照 |
| RickyTaylor4773 | 6.37h | ok | 200 | 对照 |
| AdamAdams2659 | 6.36h | ok | 200 | 对照 |
| **KirstenScott5455** | **5.68h** | **ok** | **200** | **[FAST+FRAME]** |

> browser so 账号 6.4h 全活; fast+frame 样本 5.68h ok——长活持续确认(已跨 6h)。

### 2026-08-12 ~01:50(定时 12)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 6.89h | ok | 200 | 对照 |
| JoseWhitney3017 | 6.88h | ok | 200 | 对照 |
| RickyTaylor4773 | 6.88h | ok | 200 | 对照 |
| AdamAdams2659 | 6.87h | ok | 200 | 对照 |
| **KirstenScott5455** | **6.18h** | **ok** | **200** | **[FAST+FRAME]** |

> browser so 账号近 7h 全活; fast+frame 样本 6.18h ok——长活持续确认。

### 2026-08-12 ~02:25(定时 13)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 7.38h | ok | 200 | 对照 |
| JoseWhitney3017 | 7.37h | ok | 200 | 对照 |
| RickyTaylor4773 | 7.37h | ok | 200 | 对照 |
| AdamAdams2659 | 7.36h | ok | 200 | 对照 |
| **KirstenScott5455** | **6.68h** | **ok** | **200** | **[FAST+FRAME]** |

> browser so 账号 7.4h 全活; fast+frame 样本 6.68h ok——长活持续确认。

### 2026-08-12 ~03:00(定时 14)

| 账号 | age | status | http | 标记 |
|---|---|---|---|---|
| JasonCopeland6778 | 7.91h | ok | 200 | 对照 |
| JoseWhitney3017 | 7.90h | ok | 200 | 对照 |
| RickyTaylor4773 | 7.90h | ok | 200 | 对照 |
| AdamAdams2659 | 7.89h | ok | 200 | 对照 |
| **KirstenScott5455** | **7.20h** | **ok** | **200** | **[FAST+FRAME]** |

> browser so 账号 7.9h 全活; fast+frame 样本 7.20h ok——长活持续确认。
