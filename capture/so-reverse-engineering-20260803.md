# so（session observer）逆向：行为字段全解码（2026-08-03/04）

## so 生成机制（已反混淆）

- `se(flow, challenge)`：验证 `challenge.so.{required, collector_dx, snapshot_dx}` → 存 `cachedSOChatReq` → `Et(challenge)`
- `Et(challenge)`：`jt(collector_dx, $(challenge))` —— **异步**启动采集器字节码（270 条指令）
- `sessionObserverToken(flow)`：`Nt(snapshot_dx)` = `jt(snapshot_dx)` —— so 值 = 解释 `snapshot_dx`（523 条指令）
- 两者都用 `jt`（so 的字节码 VM，操作码表 St，与 t 链 bn 同构）
- `ce({so, c}, flow)`：加 `id`(oai-did) + `flow` → JSON = openai-sentinel-so-token 头

## collector_dx（270 条）：设置 36 个 `window.__oai_so_*` 行为字段 + 注册监听器

监听器：`window.addEventListener(keydown/pointermove/click/scroll/paste/wheel, handler, true)`

## snapshot_dx（523 条）：读取 `__oai_so_*` 编码进 so

读 `window.__oai_so_h / hi / hp / hw / s / k / ...`，XOR/BTOA 编码，移除监听器

## vm 现状：字段全 null

实测 vm solve 后 36 个 `__oai_so_*` 字段**全是 null/0** —— 采集器初始化了字段但行为事件没更新它们。

**根因**：`Et→jt(collector_dx)` 是**异步**的（`void Et(n)` 不 await），监听器在指令 216-222 才注册。
solve 里 `simulateBehavior` 同步触发事件 → 事件在监听器注册**之前**到达 → 无效果。

**尝试修复失败**：se 后加 600ms 延迟等 collector 完成 → **挂起**（collector 与 snapshot 的 jt 共享全局 St/Y/Ct/At，互相干扰）。

## 结论

so 的行为段（36 字段）在 vm 里是空的。修复需要：
1. 解决两个 jt 共享状态下的异步编排（collector 完成→事件→snapshot）
2. 或手动复刻 collector 的哈希逻辑，直接写 `__oai_so_*` 字段（需先理解字段格式）

即使修好，存活仍不保证（t 长度 / IP 簇 / 收件箱等信号）。

## 交付物

- `capture/deobf_chain.py` — 通用解码链去混淆（pe/Tt 表）
- `data/collector_dx_program.json`、`data/snapshot_dx_program.json`
- adapter 的 `oai_so` 调试输出 + simulateBehavior（实验性）
