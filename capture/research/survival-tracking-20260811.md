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

