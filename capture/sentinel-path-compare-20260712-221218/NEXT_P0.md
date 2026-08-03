# 第 0 步结论 + P0 最小方案

时间：2026-07-12 22:12  
对照产物：`compare.json` / `COMPARE.md`  
脚本：`capture/compare_sentinel_paths.py`

## 实测三路径（同 device_id / flow=oauth_create_account）

| mode | 含义 | has_so | t | 备注 |
|------|------|--------|---|------|
| **pow** | **当前注册主路径** | **false** | **空串 t_len=0** | `auth.make_sentinel_headers` → `SentinelPoW` |
| file | Node + 预拉 challenge | false | t_len=132 **SyntaxError** | 旧路径，假 turnstile |
| url | Node + 1789 闭环 | false | t_len=832 **非 SyntaxError** | t 修好了，仍无 so |

三条 **全部 has_so=false**。  
与历史 `so-research-20260712` / `CLOSED_LOOP.md` 一致；并钉死：

> **配置里 `sentinel_challenge_mode=url` 已不是注册实际路径。**  
> 实际是 **纯 Python PoW**。后续存活结论必须以 `mode=pow` 为基线。

## 决策

- 不再用改 PoW / 改 Node VM / 伪造 so 来「凑 has_so」。
- 进入 **P0：浏览器真页只产 sentinel（token + so）**，协议只做 OTP/create。

## P0 范围（最小、单变量）

### 要
1. 真 Chrome/Chromium（可 CDP；复用 `capture/cdp_capture` 思路）
2. 打开与 create 同源上下文：`https://auth.openai.com/about-you`（或能加载 sentinel sdk 的 auth 页）
3. 调用真实 `SentinelSDK.token("oauth_create_account")`（或页面内等价路径）
4. 导出两个 header 材料：
   - `openai-sentinel-token` = `{p,t,c,id,flow}`（`t` 非 SyntaxError）
   - `openai-sentinel-so-token` = `{so,c,id,flow}`（so 真值，wrapper ~2.9k / so ~600 量级对齐 Jennifer）
5. 协议侧接缝：仅在 `make_sentinel_headers` 或 create 前注入「外部提供的 token/so」；**默认仍 pow**，浏览器路径 opt-in
6. 假 so 过滤逻辑 **保持**（SyntaxError / `MDogU3ludGF4` 丢弃）

### 不要
- 全浏览器走完注册（扩大变量）
- 手写 / 拼接假 so
- 关掉假 so 丢弃
- 同根已污染邮箱做 P1
- 先动 post_login / finalize 当银弹

### 验收（零耗号即可）
连续 3 次：
- `t` 非 SyntaxError，长度显著 > 132
- `has_so=true`，`so_header_len` 量级接近 Jennifer（~2900）
- 不依赖协议 create 是否 200

### P1 门槛（有 so 之后）
- **新根邮箱**单号
- A：真 so + 现 post_login  
- B：无 so（pow）对照  
- retest 15/30/40/120；原始 `token_revoked` / check body

## 接缝草图（实现时再写代码）

```
[Browser helper] → token_json + so_header
        ↓ opt-in
register_one → create_account(sentinel_header, so_header)
        ↑ default
   SentinelPoW (现状)
```

配置建议（未来，未实现）：
```yaml
protocol:
  sentinel_source: pow   # pow | browser
  # browser 时：CDP endpoint 或 helper 脚本路径
```

## 明确禁止清单（沿用）
1. 假 so 当真值发出  
2. 为凑 has_so 关过滤  
3. 同根 JohnOwens 类邮箱盲刷  
4. 无真 so 时宣称「延迟 ban 已解决」
