# quickjs 跨 token 自洽性实证（A1/A2/A3 实锤）+ vm 污染泄漏（2026-08-03 深夜）

## 背景

好友对项目做防守方视角审计，提出 A 系列信号（令牌内部自相矛盾）：
- A1 假 navigator 属性（`plugins/mimeTypes/vendorSub` 报 undefined）
- A2 同 device_id 多次 token 字段漂移（会话内不自洽）
- A3 time_origin 漂移

他分析的是默认 pow 路径（`sentinel.py:_config` 手搓数组，已逐条命中）。
本文件实证：**同样的信号在 quickjs 真实 SDK 路径上也成立**。

## 方法

`capture/probe_quickjs_consistency.py`：同 device_id 连续 3 次独立 requirements
（每次 = 独立 Node VM 进程，与「requirements 与 solve 两次进程」同机制），
解码真实 SDK 产出的 p 逐字段对比。

真实 SDK 的 p 可解码为 **25 元素 JSON 数组**（非二进制；服务端 challenge token
反而是二进制 1604B，格式不同）。

## 实证结果

| 字段 | 三次取值 | 判定 |
|---|---|---|
| [1] 时间串 | 三次相同（同秒） | 稳定 |
| [3] | 35 / 19 / 5 | 漂移 |
| [9] | 6 / 3 / 1 | 漂移 |
| [10] | `'undefined'` ×3 | **A1 实锤** |
| [11] | `'location'/'addEventListener'/'createElement'` | 漂移（疑似 window 键随机采样） |
| [12] | `'__sentinel_init_pending'`/`'getComputedStyle'`/`'navigator'` | **vm 污染泄漏** |
| [13] | 36361.9 / 36430.48 / 36478.51 | 漂移（performance.now） |
| [14] | 3 个不同 UUID | 漂移（crypto.randomUUID 每次现造） |
| [17] | 1785765986356.9 / …6425.5 / …6474.5 | **A3 实锤**（time_origin 漂 ~100ms） |

## 结论

1. **A1 实锤**：vm 的 navigator 没定义 plugins/mimeTypes/vendorSub → SDK 如实报
   `undefined`。真 Chrome 里这些是 object/array。
2. **A2 实锤**：[3]/[9]/[11]/[12]/[14] 每次进程都变，尤其 [14] 是每次现造的
   crypto.randomUUID，非 device_id。
3. **A3 实锤**：[17]（epoch ms time_origin）每次漂 ~100ms。真浏览器 timeOrigin
   是页面加载常数。同一次注册的 requirements token 与 solve token 必然不一致。
4. **爆炸性发现（新）**：[12] 采样到了 `__sentinel_init_pending` —— installRuntime
   注入 globalThis 的自定义变量（openai_sentinel_quickjs.js）。SDK 疑似对
   window 键做随机采样，vm 污染直接进指纹。防守方扫 `__sentinel_`/`__debug`/`__vm_`
   前缀即可秒杀全部 quickjs token。真浏览器窗口绝无这些键。

## 可修（保真）vs 结构性（不可修）

可修：补 navigator.plugins/mimeTypes/vendorSub；会话内固定 time_origin；
隐藏/清除 `__sentinel_*`/`__debug*`/`__vm_*` 全局；[14] 用 device_id 而非 randomUUID。

结构性（与模式无关，改不掉）：B 传输矛盾 / C2 单一指纹巨簇 / D1 共享收件箱星形图 /
E1 零资源直线流。

## 交付物

- `gptreg/sentinel_quickjs.py` — 每次成功注册 dump request_p/final_p/so → data/solved_tokens/（data/ 已 gitignore）
- `capture/decode_solved_tokens.py` — 解码对比脚本（真实注册后跑）
- `capture/probe_quickjs_consistency.py` — 本次探针（可复跑）
