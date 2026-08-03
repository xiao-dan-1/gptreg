# chatReq.so 观测探针（2026-07-12）

## 做了什么

只加诊断，不产 so、不改默认 pow：

| 位置 | 行为 |
|------|------|
| `gptreg/sentinel.py` | `summarize_chatreq` / `log_chatreq_obs`；`SentinelPoW.build` 拉 req 后打日志 |
| `gptreg/auth.py` | `request_sentinel` 统一观测；pow 路径 meta 挂 `chatreq`；`so_required` 且无 so 时 warning |
| `gptreg/pipeline.py` | `sentinel_obs.chatreq` 落盘 accounts |

日志前缀：`[Sentinel/chatReq]`

字段：`keys / requires / so_field / so_required / collector_dx_len / so_keys / turnstile_* / pow_* / c_len / persona`

## 活探针（零耗号，pow POST /req）

产物：`chatreq_obs_live.json`

```text
flow=oauth_create_account http=200
keys=[expire_after, expire_at, persona, proofofwork, so, token, turnstile]
requires=[pow, turnstile, so]
so_field=True so_required=True
collector_dx_len=16760
so_keys=[collector_dx, required, snapshot_dx]
turnstile_required=True dx_len=20844
pow_required=True difficulty=061a80
persona=chatgpt-noauth
```

## 对照

| 样本 | has so 字段 | so.required | collector_dx |
|------|-------------|-------------|--------------|
| 资料 `input_runtime.json` | **否** | n/a | 无 |
| 本机 live pow `/req`（本次） | **是** | **true** | ~16k + snapshot_dx |

含义：

1. 服务端**有时**会要求 so（本次 live 明确 required）。
2. 资料包样例是**无 so 的旧/窄样本**，不能代表当前 create challenge。
3. 我们 pow 主路径：即使 `so_required=true`，仍只交 `{p,t="",c,id,flow}`，**不跑 collector/snapshot** → 与「无 so 注册能 200 但易 delayed ban」假说一致，待 P1 存活验证。
4. 真 browser 路径不依赖我们解析 chatReq.s o，而是页内 `sessionObserverToken`（内部用 snapshot_dx 一类）。

## 禁止

- 不因 so_required 自动伪造 so
- 不关假 so 过滤
- 不把 jsdom 并进主路径

## 附：顺手修复

`SentinelPoW._solve_pow` 失败回退串曾被损坏为非 `gAAAAAB` 前缀，已恢复为 `gAAAAAB` + b64("e")。
