# create `registration_disallowed` 根因研究（2026-07-13）

目标：找 **OTP/about-you 已过、create 400 `registration_disallowed`** 的根，不盲改 sentinel。  
方法：本地/外网对照 + 已有样本排序假设 + 单变量实验（先设计后烧号）。

---

## 1. 现象（本机今日 + 历史）

| 时间 | 邮箱根 | mode | has_so | create | 备注 |
|------|--------|------|--------|--------|------|
| P1 | LeslieChavez6274 | pow | — | **400 disallow** | 同 log 约 1min 后 Eric pow **200** |
| P1 | EricWilliams3405 | pow | false | **200** | ≥124min 活 |
| P1 | BrandonNichols1400 | browser | true | **200** | ≥126min 活 |
| 今日 | LarryHoffman7534 | pow | false | **400 disallow** | OTP/about-you ok |
| 今日 | TracyHenry4340 | pow | false | **400 disallow** | 同上 |
| 今日 | JenniferPhillips9261 | browser | true / 2674 | **200** | t0 health ok |

共同：协议步完整；死在 `POST .../create_account`；body 仅 `{name, birthdate}`。

代理：辣椒 US + chain 7890 出口正常（可换 IP）。

---

## 2. 大佬 / 资料怎么做（对照）

| 来源 | create sentinel | so | name | birthdate | 对 disallow |
|------|-----------------|-----|------|-----------|-------------|
| **starmiaoa/chatgpt-register-k12** | 纯 Python PoW，`t=""` | **无** | 真名池 James/Smith… | 随机日 | create 非 200 当「域名/账号失败」类抛错；**无 so 银弹** |
| **Sake-79/sub2api** | 同族 k12 | 无 | 同 | 同 | 同 |
| **leetanshaj/openai-sentinel** | 仅 token 库 | 无 | — | — | 不管注册 |
| **本地 k12-register-dist** | Node/opportunistic so | 有则带 | Kevin/Smith 池 | `YYYY-01-01`（由 age 推） | 只 warm 重试 **invalid_auth_step**，**不**特判 disallow |
| **资料/chatgpt_register** | get_token + get_so_token | 试图 so（**collector 错位**） | 真名池 | age→生日 | disallow **同 body 重试 3 次 sleep 2s**（无换根/换 so） |
| **我们** | 默认 pow；browser opt-in | 真 so 仅 browser | **乱码字母名** | 随机 YYYY-MM-DD | 失败即停（可 `--continue-on-fail`） |

**外网共识（2026-07）：**

1. 公开项目 **几乎全靠 pow、无 so** 跑通 create（密码流或 OTP 流）。  
2. **没有**「靠 sessionObserver 解 registration_disallowed」的公开方案。  
3. 资料包对 disallow 的「解法」= **硬重试**，不是协议升级。  
4. 真 so 仍是我们 P0.5 独有通路；P1 已证它 **不是 2h 存活开关**。

---

## 3. 姓名假设：可排除为「充分条件」

历史 **成功号** 的 name 全是乱码风格（与失败号同生成器）：

| 成功邮箱 | name | mode |
|----------|------|------|
| RoelfsWida92+… | Vmtzli Ipbo | （旧） |
| ConderGord45+… | Evxlcvl Adjai | |
| JohnOwens… | Gyerc Bfqv / Tyfri Uidk | pow 无 so |
| Brandon… | Ufyltvv Wvgyv | browser |
| Eric… | Cgzvcg Slzvkn | **pow 无 so** |
| Jennifer… | Jvpqy Isytz | browser |

失败号同样是 `Uqaexae Beuo` / `Hhuy Aowgcke` 一类。  
→ **乱码名不是 disallow 的充分条件**；对齐大佬真名池最多算卫生项，**不能当根因修复**。

---

## 4. 假设排序（证据级）

| 优先级 | 假设 | 支持 | 反对 | 判定 |
|--------|------|------|------|------|
| **H1 邮箱根身份** | 根邮箱信誉/池质量 | Leslie vs Eric 同 pow 同 log 一死一活；今日 2 根 fail / 1 根 ok | — | **主因候选** |
| **H2 代理/出口** | 某 IP 被 create 拒 | 每次新 sid；探测 US 正常 | 同链路上 Jennifer 成功 | 次要；需 **同根不可**，用相邻时间 A/B 出口对照 |
| **H3 create 真 so** | 无 so → disallow | 今日 2 pow fail + 1 browser ok | **Eric pow 200**；P1 设计已否「无 so 必 create 挂」 | **弱相关 / 混杂**；需配对实验 |
| **H4 姓名/生日格式** | 乱码/日期触发 | 大佬用真名 | 成功号全乱码 | **基本排除** |
| **H5 协议步缺失** | 没 warm about-you 等 | — | 日志 OTP→about-you→create 齐全 | **排除** |
| **H6 假 t / 旧路径** | file 假 turnstile | 历史短命可能 | 当前默认 pow `t=""` 与 starmiaoa 同族且 Eric 长活 | 非今日 disallow 主因 |

**一句话：**  
今日「pow 连拒、browser 成功」**不能**直接写成「缺 so → disallow」——邮箱根未控制。主因仍应先按 **H1 邮箱根** 查。

---

## 5. 和 P1 的边界（别搅在一起）

| 问题 | 结论来源 | 状态 |
|------|----------|------|
| create 之后 2h 会不会因缺 so 死？ | P1 A/B | **双活，so 非 2h 开关** |
| create 当时 400 disallow 是什么？ | 本笔记 | **进行中；主疑邮箱根** |
| 默认是否改 browser？ | P1 + 本笔记 | **否**；browser 仍 opt-in |

---

## 6. 可执行实验（先设计，用户点头再烧）

### 零耗号（先做）

| ID | 动作 | 产出 |
|----|------|------|
| Z1 | 号池 used/retrying 与成功 jsonl 对照表（根邮箱模式） | 本目录 `disallow_mailbox_table.json` |
| Z2 | 代理仅探测（已做） | US 可换 IP |
| Z3 | Jennifer retest 15/40（可选） | 存活，不解释 create |
| Z4 | 可选卫生：`random_display_name` 对齐真名池（**不声称修 disallow**） | 代码小改 |

### 耗号单变量（严格）

**E1 — create 通过率：so vs 无 so（控制「同一批、交替」）**

```
N=3 对（共 6 根，用户确认后）
奇数次: pow
偶数次: browser
同一 config/代理模板/时段
记录: email_root, mode, has_so, create_http, error_code, proxy_ip
禁止: 失败后同根重试「换 so」；禁止连刷超 N
```

判据：

| 结果 | 解释 |
|------|------|
| browser 通过率显著高 | H3 升级；仍保留默认 pow，文档写「create 拒时 opt-in browser」 |
| 无显著差异 | H3 降级；disallow ≈ H1 邮箱 |
| 全拒 | H1/H2 为主，停烧号换池/代理 |

**E2 — 仅 pow 换根基线（不引入 browser）**

```
N=3 pow only → 估当前池 create 基线通过率
```

若 E2 通过率已高，不必急着 E1。

### 明确不做

- 为 disallow 默认改 browser / 造 so / 并 jsdom  
- 资料式「同 body 重试 3 次」当根因修复（可作运维开关，另议）  
- 把 P1 存活结论改写成 create 结论  

---

## 7. 建议执行序（与用户「继续实验 + 学大佬」对齐）

1. **Z1** 落盘邮箱成败表（零耗号）  
2. 用户选 **E2** 或 **E1**（或先 Z4 卫生）  
3. 每烧 1 号立刻记表，满 N 停，写结论  
4. 结论进 `FINDINGS` / skill pitfalls，**再**谈要不要改默认  

---

## 9. E2+E1 实跑结果（2026-07-13，Z4 真名）

设计：3 对交替 `pow → browser`，共 6 根；真名池已开。  
产物：`e2_e1_results.jsonl` · `e2_e1_summary.json` · `_run_e2_e1.py`

| slot | mode | 邮箱 | name | create | has_so |
|------|------|------|------|--------|--------|
| 1 | pow | ThomasRivers7260+a36d42 | Richard Thompson | **400 disallow** | false |
| 2 | browser | JasmineMcconnell9909+1b5fcd | Kevin Price | **200 ok** | true / 2698 |
| 3 | pow | TrenhHattie36+2a54c2 | Anthony Ward | **400 disallow** | false |
| 4 | browser | BeatheFulwood6282+85b8d3 | John Brown | **200 ok** | true / 2614 |
| 5 | pow | EmbreeNicholas183+7fcc43 | Mary Johnson | **200 ok** | **false** |
| 6 | browser | BengelsdorfSalato25+bf3db9 | Sophia Ward | **200 ok** | true / 2670 |

### 通过率

| mode | ok / n | disallow | create 通过率 |
|------|--------|----------|---------------|
| **pow** | **1 / 3** | 2 | **~33%** |
| **browser** | **3 / 3** | 0 | **100%** |

### 能下 / 不能下

**能下：**

1. **真名 ≠ 解 disallow**：slot1/3 真名仍 400；slot5 真名 + pow 无 so 仍 200。  
2. **无 so 仍可 create**：slot5 Embree pow `has_so=False` → 200 + health ok → **H3「无 so 必 400」再次否证**。  
3. **本窗口 browser 通过率高于 pow**（3/3 vs 1/3）— 值得记，但 **n 小 + 邮箱根未交叉**。  
4. Z4 卫生完成；默认 **仍保持 pow**（未改策略开关）。

**不能下：**

- browser 是 create 银弹（样本混杂邮箱；P1 Eric 已是 pow 长活）  
- 默认应改成 browser（成本高；OTP 仍 pow；P1 存活无差）  
- 池子全废（pow 仍有 1/3 过）

### 更新后假设序

| 优先级 | 假设 | 状态 |
|--------|------|------|
| H1 邮箱根 | 仍主因候选 | 未单独交叉设计 |
| **H3 create so** | **本窗 browser 更好**；**非充分必要**（pow 可过） | 弱相关 / 辅助 |
| H4 姓名 | **排除** | 真名仍拒、乱码/真名皆可过 |
| H2 代理 | 未单独控 | 同链路上有成有败 |

### 策略建议（实验后）

| 项 | 决定 |
|----|------|
| 默认 `sentinel_source` | **仍 pow** |
| 生产 create 连拒 | 换根；可选 **当次** `--sentinel-source browser` 作对照，不永久改默认 |
| 造 so / jsdom | 仍禁 |
| 下一实验 | 若继续：同根不可；或更大 n；或换邮箱源 / 代理 region 单变量 |

## 10. pow 续测（2026-07-13 ~01:55，仅 pow，n=3）

号池 unused=4→跑 3；`continue-on-fail`。  
产物：`pow_baseline_cont.jsonl` · `pow_baseline_cont_summary.json`

| # | 邮箱 | 阶段 | 结果 |
|---|------|------|------|
| 1 | QuentinKaboos152+1c386e | authorize 落点 **create-account/password** →OTP | **OTP 超时** + MSMail curl35 TLS |
| 2 | ArderyAlcocer1469+0f3f1d | email-verification 后 | **SSLError curl35**（未到 create） |
| 3 | FerdolageSagehorn0127+d7d9fa | 到 create；James Thompson | **400 registration_disallowed** |

### 本窗

| 项 | 值 |
|----|-----|
| ok | **0 / 3** |
| 到 create 且 disallow | **1** |
| 未到 create（OTP/TLS） | **2** |
| 与 E2 pow 合并 | **1 / 6** 成功（仅 Embree） |
| 仅「到 create」的 pow | E2 1/3 + 续 0/1 → **1 / 4** create 过 |

### 读法

1. **勿**把 0/3 全算 pow create 废——2/3 是 Graph TLS/OTP 基建噪声。  
2. 真名 + pow 到 create 仍可 400（Ferdolage）。  
3. Quentin 落点 `/create-account/password` 异常（passwordless 预期 verification）→ 账号态分叉，先别怪 so。  
4. unused≈1 + TLS 不稳 → **暂停再烧**；先修收信 TLS / 补号。

策略不变：默认 pow；连拒可当次 browser；不造 so。

