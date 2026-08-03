# P1 存活对照：browser so vs pow（2026-07-12）

## 设计（单变量）

| 组 | sentinel | post_login | 期望 |
|----|----------|------------|------|
| A | browser 真 so | true | 若 so 是长活关键因子，应过 40–120min |
| B | pow 无 so | true | 历史对照：~7–15min `token_revoked` |

共同：新根邮箱、US 辣椒 via 7890、OTP=pow、假 so 过滤保留。

## t0 注册结果

### A — browser（成功）

| 项 | 值 |
|----|-----|
| email | `BrandonNichols1400+c688c2@outlook.com` |
| main | BrandonNichols1400@outlook.com（新根） |
| device_id | `b611a847-35d0-49f0-89e4-cc990168d777` |
| saved_at | 2026-07-12T22:43:10 |
| mode | **browser** |
| has_so | **true** |
| so_len | 2718 |
| t_len | 1412 |
| health t0 | **ok** accounts/check=200 |
| post_login | me/init/prepare 全 200；prepare `so_required=true`；finalize **skipped** |

日志：`run_A_browser.log`

### B — pow（第二次成功）

| 项 | 值 |
|----|-----|
| email | `EricWilliams3405+6af702@outlook.com` |
| main | EricWilliams3405@outlook.com（新根） |
| device_id | `cbff787d-83ca-4c40-a9ef-c41e3d58cc23` |
| saved_at | 2026-07-12T22:45:52 |
| mode | **pow** |
| has_so | **false** |
| so_len | 0 |
| health t0 | **ok** accounts/check=200 |
| post_login | me/init/prepare 全 200；prepare `so_required=true`；finalize **skipped** |

日志：`run_B_pow.log`

### B 第一次失败（耗 1 号，create 前）

| 项 | 值 |
|----|-----|
| email | LeslieChavez6274+61d836@outlook.com |
| create | HTTP 400 `registration_disallowed` |
| 原始 body | `Sorry, we cannot create your account with the given information.` / `code: registration_disallowed` |
| 备注 | 同 pow 无 so；Eric 约 1min 后同协议成功 → **身份/邮箱根优先，非 so**。见 `RESEARCH_123.md`。 |

## Retest

```bash
python capture/retest_health.py --only BrandonNichols1400 --only EricWilliams3405 \
  --loop-min 5 --until-age 120
```

endpoint：`accounts/check` + `/me`  
产物：`output/retest_*_history.jsonl`、`retest_loop.log`、`p1_final_snapshot.json`

---

## 最终结论（loop 已 stop，2026-07-13）

`retest_health.py --until-age 120` → **exit 0**，日志：`reached until-age=120.0, stop`

### 结果表

| 组 | mode | has_so | 最终 age | health | deactivated | 样本数 | 失败 |
|----|------|--------|----------|--------|-------------|--------|------|
| **A** | browser | true / 2718 | **126.7 min** | ok/200 | false | 25 | **无** |
| **B** | pow | false / 0 | **124.0 min** | ok/200 | false | 25 | **无** |

时间线（每 ~5min，**全 ok**）：

```text
A: 3.2 … 121.5 … 126.7  ≥120 ✓
B: 0.6 … 118.9 … 124.0  ≥120 ✓
```

**结局类型：双活（≥120min；create 有无 so 无存活差）**

快照：`p1_final_snapshot.json`

### 能下（本 1+1 + 本环境）

1. **「无 so 必 ~7–15min `token_revoked`」不成立**  
   历史 JohnOwens 无 so 快死；本 B 无 so 活过 ~2h → 短窗死**不是**缺 so 的充分条件（更可能叠加邮箱根/代理/时期）。
2. **create 有无 so 在 ~2h 内未拉开存活差**（n=1 对照）。
3. **半截 post_login**（prepare 有、**无 finalize**）两边均未在 2h 内拖死。
4. **`registration_disallowed` ≠ 无 so**（Leslie 拒 / Eric 过，同 pow 无 so）。

### 不能下

- so 对 **>2h / 数天** 长活有无用（未测）
- n=1 的普遍性（别的根邮箱/代理可能不同）
- chat **真 finalize** 是否加成（故意 skip，未做 A/B）
- so 质量（headed 长度差）是否影响 ban（未单变量）

### 策略（P1 后）

| 项 | 决定 |
|----|------|
| 默认 `sentinel_source` | **仍 `pow`** |
| browser 真 so | **opt-in 保留**（技术通路已通；非 2h 存活必需） |
| OTP | 始终 pow |
| 假 so 过滤 | **保留** |
| 因 `so.required` 改默认 / 造 so | **否** |
| 资料 jsdom / collector 当 so | **否** |
| 假 finalize | **否** |
| P2 身份隔离 | **不因本 P1 紧急上**；若后续批量短死再开 |
| chat 真 finalize 研究 | **可选、另开关、默认关**（次优先） |

一句话：**本环境 2h 内，pow 无 so 与 browser 有 so 同样能活；真 so 是可选增强，不是短窗生死开关。**

---

## 中期快照（归档，~00:04）

A ~80min / B ~78min 双活 — 与最终一致方向。

## 并行零耗号研究（索引）

| 笔记 | 结论摘要 |
|------|----------|
| `CHATREQ_OBS.md` | live `/req`：OTP+create **`so.required=true`**；密码流可无 so |
| `SDK_SESSION_OBSERVER.md` | `sessionObserverToken` = `Nt(snapshot_dx)`；须先 `token()` |
| `POST_LOGIN_PREPARE.md` | create so ≠ chat prepare 过关；finalize 我们 skip |
| `REF_chatgpt_register.md` | 资料图对；jsdom 误 collector → 禁并 |
| `RESEARCH_123.md` | headed so 略长；外网无 so / 错 collector；Leslie=身份类 |
| `research_pack/*` | multi_flow + prepare live |
| `browser-so-harvest-20260713-002635/` | headed 2/2 has_so（484/508） |

### 关键对齐

```text
token()                 → {p,t,c} 永无 so；ke() 缓存 challenge
sessionObserverToken()  → Nt(so.snapshot_dx) → {so,c,id,flow}
资料 jsdom              → 错跑 collector_dx
chat prepare            → 另门面；A/B 均 so_required 且 skip finalize
P1 2h                   → 有无 create so 双活
```

## 号池

- A 成功 1；B 失败 Leslie 1 + 成功 Eric 1  
- 合计 3 根；**无证据前不盲刷**

## 下一步（P1 收口后）

1. ~~可停 retest loop~~ **已 stop**（until-age=120）
2. ~~文档/注释与策略表对齐~~ **已做**（README / config.yaml / cli / 模块 docstring）
3. 批量生产继续 pow；需要时再 `--sentinel-source browser`
4. 若线上再现 7min 死 → 优先查 **邮箱根/代理**，不要先怪缺 so
5. 可选：chat 真 finalize 研究（另开，不默认）
6. **create `registration_disallowed` 根因线**（与 P1 存活分离）→ 见 `RESEARCH_CREATE_DISALLOW.md`  
   - 大佬：starmiaoa 无 so；资料仅硬重试；公开方案无 so 银弹  
   - 成功号亦乱码名 → 姓名非充分条件  
   - 主疑 **邮箱根**；今日 pow×2 拒 / browser×1 成 **混杂未控根**