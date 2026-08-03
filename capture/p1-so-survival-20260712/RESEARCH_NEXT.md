# 零耗号研究包（P1 retest 并行，2026-07-12 23:25）

## 1. 多 flow chatReq（live pow `/req`）

同 device_id，辣椒 US via 7890：

| flow | so.required | turnstile | pow | collector_dx | snapshot_dx | turnstile.dx | c_len |
|------|-------------|-----------|-----|--------------|-------------|--------------|-------|
| authorize_continue | **true** | true | true | ~17.3k | ~18.5k | ~21.0k | 2060 |
| oauth_create_account | **true** | true | true | ~17.5k | ~18.5k | ~22.6k | 2060 |
| username_password_create | **无 so 字段** | true | true | 0 | 0 | ~20.5k | 1740 |

要点：
- OTP / create 两档 **都会要求 so**（当前环境）。
- 密码注册 flow **不带 so 挑战**（只要 pow+turnstile）。
- 资料 zip 的 `input_runtime.json` 无 so = **旧/窄样本**，不能代表 live create。

产物：`research_pack/multi_flow_chatreq.json`

## 2. dx 形态（不执行，只 peek）

三者均可 base64 解码，解码后是 **二进制/混淆脚本**，非明文 JS：

| 字段 | 用途（sdk 线索） | 特征 |
|------|------------------|------|
| `turnstile.dx` | 产 token 的 `t` | 前缀 `PBp5bWFw…` → b64 后 `<ymapqy…` |
| `so.collector_dx` | SO 采集器 | 前缀 `PBp5bWF0…` → `<ymat{y…` |
| `so.snapshot_dx` | **sessionObserver 主路径** | 前缀 `PBp5bWFy…` → `<ymarqy…` |

sdk.js 中 `sessionObserverToken` 文案：  
`sessionObserverToken() should not be called from within an iframe.`  
→ 与「必须真页 / 非 iframe」一致。

## 3. so 字符串形状（真值对照）

| 来源 | so 字段 len | wrapper | so 前缀 | c 与 token.c |
|------|-------------|---------|---------|-------------|
| 本机 harvest headless | 464–480 | ~2.6–2.7k | `TRsZ…` / `TRMZ…` | **同一 c** |
| P1 A Brandon create | — | **2718** | （未落盘原文） | 同 challenge |
| Jennifer create | **612** | **2914** | `TRQZ…` | 同 shape |

- 真 so ≈ base64(二进制)，printable_ratio ~0.3–0.5（非明文）
- 假 so = `SyntaxError` / `MDogU3ludGF4…`（已过滤）
- **c 必须与 token 同源 challenge**（harvest 验证 same c）

## 4. A/B 注册元数据差异（非存活）

| | A Brandon browser | B Eric pow |
|--|-------------------|------------|
| has_so / so_len | true / 2718 | false / 0 |
| t_len | 1412 | null/空 |
| OTP sentinel | pow | pow |
| create mode | browser | pow |
| post_login | ok | ok |
| prepare so_required | **true** | **true** |
| finalize | 两边都 `skipped_no_real_pow_turnstile` | 同 |

→ post_login **prepare 层也要 so**，两边都没 finalize 真 turnstile/so；  
若 delayed ban 与 chat 侧 sentinel 有关，这是**并列假设**（次于 create 双头）。

## 5. 日志缺口

A/B 注册日志在 chatReq 探针合入**之前**，故 run_*.log **没有** `[Sentinel/chatReq]`。  
下次注册会有 `so_required` / `collector_dx_len`。

## 6. 仍可研究（零耗号排序）

1. **sdk 反混淆最小切片**：`sessionObserverToken` → 如何消费 `snapshot_dx`（只读，不写产 so）
2. **headed vs headless so 长度**：是否逼近 Jennifer 612（仍 harvest only）
3. **post_login prepare 真挑战**：是否与 create 同一 so 体系；finalize 要什么（禁盲 finalize）
4. **密码 flow 无 so** vs OTP/create 有 so：协议差异文档化
5. **registration_disallowed**：Leslie 根域名/代理对照（读历史，不新耗号除非用户要）
6. **外部参考** leetanshaj / starmiaoa 是否处理 snapshot_dx（web 对照）
7. 等 P1 60/120 再下存活结论

## 禁止（本包确认）

- 不在 Node/jsdom 里 eval collector/snapshot 当主路径
- 不伪造 so
- 不因 so_required 改默认 pow
- 不自动烧号池
