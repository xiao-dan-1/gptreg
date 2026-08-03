# turnstile 指纹计算解码（三级字节码，2026-08-03）

## 三级结构

1. **一级**（dx 解密，key=request_p）：88 条指令。建别名表，读 window.Reflect/Object/Math/localStorage/performance/document/history/navigator/screen，XOR/btoa 编码，加载二级 blob。
2. **二级**（blob2 解密，key="13.08"）：384 条指令。**真正的指纹计算**（见下）。
3. **三级**（blob1，key 待定）：仅当时序分支触发才加载。

## 二级程序读取的指纹（按序）

| 读取 | vm 状态 |
|---|---|
| screen.availWidth / availHeight / availLeft / availTop | ✅ 已对齐（保真修复）|
| screen.colorDepth / height / width / pixelDepth | ✅ 已对齐 |
| navigator.deviceMemory | ✅ 已对齐 |
| document.location | ✅ 已对齐 |
| **Object.keys(localStorage)** | ❌ **vm 返回方法名/空，真浏览器返回实际存储键** |
| performance.now() | ✅ 真实时钟（多次）|

每次读取间有**时序分支**：`ABSCOND(|now − prev| > 2000ms) → 加载 blob1（第三层）`。
vm 执行太快（微秒级），永远 < 2000ms → 永不触发慢路径 → t 更短。

## 对 t 长度差的解释

vm t 短 ~130-200 字符的可能原因：
1. **时序分支**：真浏览器（含渲染/网络/GPU）可能 >2000ms 触发 blob1 → 更长 t；vm 恒快 → 短路径
2. **localStorage keys 为空**：vm 的 Object.keys(localStorage) 是空/方法名，真浏览器是实际数据

## 下一步

1. 捕获真浏览器 auth.openai.com 的 localStorage 实际键值，喂给 vm
2. 验证时序分支：加速/延迟 vm 的 performance.now() 使 |now−prev| > 2000ms，看 t 是否变长
3. 若 t 变长接近浏览器 → 两个缺口确认，补上后 t 结构对齐

## 工具

- `capture/vm_simulate.py` — 字节码模拟器（含一级别名表）
- `data/dx_program.json`（一级）、`data/blob2_program.json`（二级）
