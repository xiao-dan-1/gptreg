# 选项 B：keystream 逆向 + 伪造指纹（第一轮，2026-08-03 深夜）

## 目标
逆向 turnstile keystream 生成，伪造渲染指纹，使 vm t/so 通过存活深校验。

## 第一轮排除的假设

| 假设 | 结果 |
|---|---|
| `_n` 读渲染指纹（canvas/WebGL/字体）| **排除**：debug solve 全读取显示 `_n` 只读基本 navigator 属性（userAgent/language/languages/virtualKeyboard/hardwareConcurrency/deviceMemory/vendor/platform/maxTouchPoints）+ document 结构，**无任何 getContext/canvas/字体读取** |
| widget 执行是缺口 | 已排除（见 turnstile-widget-20260803.md）|
| so 行为段空 = 没喂行为事件 | **排除**：给 vm 模拟 pointermove/keydown/scroll/wheel/click（sync + 显式 timeStamp），so 未变长（392 vs 基线 404）|

## 确认的结构性缺口：t 长度

**dx 对比**（challenge 复杂度驱动）：
- 浏览器：dx_len=21520 → t=1176 字符
- vm：dx_len=20224/21908（**相近**）→ t=932-1048 字符

相同复杂度 challenge，vm t 恒短 ~130-200 字符。**缺口是结构性的，不是 challenge 驱动。**

## t 结构（XOR 流加密）

- 两个 t 都有「明文为 0 时 keystream 显形」的重复模式：浏览器 `xxort`（offset 704），vm `tyosr`（offset 80）
- vm t 在 offset 80 就有零填充区，浏览器 offset 704 才有 → vm 的指纹数据段更短
- 头部字节变化（'B'/'N'/'H'），结构相同

## 下一步（第二轮候选）

1. 逆向 `_n` 证明生成：为什么 vm 证明更短（少一段指纹数据）。需反混淆 sdk 的 `_n`。
2. 验证 t 长度差是否真导致存活死亡（对照实验：不同 IP + 不同收件箱 + browser-like 流程注册 vm 账号）。
3. so 长度差（392-404 vs 480）是否也结构性（需同 collector_dx 对比，尚未做）。

## 交付物

- `vendor/sentinel/openai_sentinel_quickjs.js` — 事件系统（真 addEventListener 记录 + dispatch 触发）、行为模拟（simulateBehavior，实验性）、`_n` 全读取诊断
- `data/turnstile_main.js`、`data/vm_so_sim.json` 等
