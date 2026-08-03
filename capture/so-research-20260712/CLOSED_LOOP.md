# k12 challenge 闭环对齐 — 2026-07-12

## 改动
- 新增 `gptreg/sentinel_proxy.py`：本地 `127.0.0.1:1789` curl_cffi 中转 sentinel/req（对齐 k12 `_sentinel_proxy.py`）
- `generate_sentinel_token_via_node` 支持 `--challenge-url`（SDK 自带 p 拉 challenge）
- `protocol.sentinel_challenge_mode`: `url`（默认）| `file`（旧）
- 假 so 丢弃策略不变

## 零耗号 A/B（同 device_id / flow=oauth_create_account）

产物：`closed_loop_compare.json`

| 模式 | keys | has_so | t_len | t 形态 |
|------|------|--------|-------|--------|
| **file**（旧：Python 预拉 challenge） | p,t,c,id,flow | **false** | 132 | **`0: SyntaxError: Expected ',' or ']'...`** |
| **url**（k12 闭环：runner 自拉） | p,t,c,id,flow | **false** | **804** | **二进制样真值，非 SyntaxError** |

## 结论
1. **闭环有效改善 turnstile `t`**：file 假 t → url 真 t 形态。说明「SDK 自己的 p 与 challenge 绑定」这条 k12 设计点成立。
2. **仍无 so**：闭环不能单独解决 sessionObserver / 行为快照。与 Jennifer 真 so≈612 差距仍在。
3. **默认切到 url**：注册路径已用闭环；`file` 保留回退。
4. **禁止**：把无 so 当成功、伪造 so、关掉假 so 过滤。

## 对延迟死亡的含义
- 先前 Step A/B 用的是 **file 模式假 t + 无 so** → 7–8min `token_revoked`
- url 闭环至少修好了 **t**；**so 仍缺**，是否拉长存活需 **新根邮箱单号** 复测（勿同根 JohnOwens）

## 下一步
1. 有新根邮箱：`sentinel_challenge_mode=url` + 现有 post_login，单号注册 + 15/30/40 retest
2. so 仍无 → 浏览器辅助产 so（Playwright 真页），不在 VM 里硬造
