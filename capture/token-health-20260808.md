# 外部 token 测活 2026-08-08（18 个 icloud 账号 access_token）

## 结果

| 状态 | 数量 | 账号 |
|---|---|---|
| ✅ ok（存活） | 5 | wait.mentees_8b / kippers.barb-0o / 23.ploy.saints / derbies-flouncy55 / 27_finer_biscuit |
| ❌ invalidated（吊销） | 10 | 50.flue_raspier / payout_redraft.2f / sachem-detox77 / wicket-98largess / dado_dye0z / 61.earbuds.kimchis / globes_variant.3e / usher-95.tenders / 23.feet-expat / 17society.vested |
| ⚠️ token_expired | 2 | album_clarity_3b / snapper.micros.97 |
| ⚠️ throttled（限流, 非吊销） | 1 | gradual.fisheye3g |

## 错误语义

- `unauthorized_unknown` = "Could not parse your authentication token. Please try signing in again." → **token 被吊销/失效**, 需重新登录
- `token_expired` = 服务端判定过期(即使 JWT exp 还在未来) → token 已被轮换, 旧 token 作废
- `throttled` (503 concurrency_limit) = 并发限流, **非吊销**, 可重测

## 关键观察

1. **吊销率 55%**（10/18 invalidated）—— 与"假 so 账号被删"实证一致, 印证 so 必须真实
2. **token_expired 的两例** exp 都在未来但服务端判过期 → 典型"已轮换旧 token"
3. **限流 1 例**（gradual.fisheye3g）—— 网络缓解后可能存活, 未计入死亡

## 说明

- 这批是 icloud 邮箱的 token（非本机注册产出），用途/来源由用户提供
- 工具：`capture/check_raw_tokens.py`（从 stdin/文件读 JWT, 自动去空白, 逐测活）
