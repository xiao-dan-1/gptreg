# so 结构对比学习：多组样本分析（2026-08-03/04）

## 样本

浏览器 so 值 5 个（464-508 chars）+ vm so 值 3 个（392-404 chars）

## 分析结果

| 分析 | 结果 |
|---|---|
| 长度 | vm 恒短 **52-80 bytes**（294-301 vs 346-381）|
| 头部 | 首字节跨样本变化（4d/49/4c/42/48），两组都覆盖 42-4d → 非固定版本号 |
| 重复周期 | 两组都无强字节周期（浏览器密集数据；vm 也非纯零填充）|
| 两两异或 | 浏览器样本密钥各不相同（零比例 6-19%）→ 无法提取共享明文，so 用每 challenge 独立密钥 |
| **行为注入测试** | **注入 4 个 `__oai_so_*` 字段（lx/ly/sp/spt）→ so 从 ~400 涨到 440 chars（+40）** |

## 核心结论

1. **行为数据确实影响 so 长度**——注入 4 个字段就 +40 chars，方向正确
2. 若 36 个字段全填入合理行为值，so 很可能达到浏览器长度（464-508）
3. **但 collector 异步覆盖**：注入后大部分字段被 collector 的 jt 重置回 null
   - 存活的 4 个（lx/ly/sp/spt）是 collector 初始化为 0 的坐标/滚动字段
   - 哈希字段（h/hp/hw/k/s）被 collector 初始化 null 后覆盖我的值

## 待解决

让全部 36 字段存活：需在 collector 的 jt 完成后注入（异步时序工程），或改 collector 的 null 初始化。

## 意义

「复刻 collector / 填充行为字段」方向被验证有意义：so 长度/结构是行为相关的，填充可让 vm so 逼近浏览器。

## 交付物

- adapter `inject_oai_so` 注入逻辑（实验性，payload 门控）
- `data/vm_so_injected.json`、`data/vm_so_decrypt_input.json`
