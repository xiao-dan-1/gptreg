# 协议替代 browser：知己知彼分析 + 指纹补全（2026-08-03 晚）

## 背景

目标：用纯协议（Node vm 跑 sdk.js）替代 `browser_sentinel` 产出**能存活**的 t/so。
已证：vm 能产 t/so（技术突破），但早期账号全部 15min-1h 被吊销（create 过、深度校验不过）。

## 方法：知己知彼（Playwright 抓浏览器全流程网络）

用 Playwright 拦截真实浏览器 SDK 的 token() + sessionObserverToken() 全程网络请求，发现：

| 发现 | 意义 |
|---|---|
| challenge 从 **`chatgpt.com/backend-api/sentinel/req`** 取（非 sentinel.openai.com）| vm 原用 sentinel.openai.com → 已改 chatgpt.com |
| sdk.js 也从 chatgpt.com 加载（frame.html iframe 上下文）| 上下文一致性 |
| 加载 turnstile widget：`cdn-cgi/challenge-platform/scripts/jsd/main.js` + `oneshot` POST | widget 执行（可能含 WebAssembly）|
| `ab.chatgpt.com/v1/initialize`、`ces/v1/rgstr` | 遥测/功能开关，非关键 |

## 指纹诊断方法（Proxy 包装记录 UNDEF）

vm 里用 `new Proxy` 包装 navigator/screen/performance/document/crypto/location，记录 solve 时所有读取 + 标记 `=UNDEF`。发现并修复：

| 缺失 | 修复 | 影响 |
|---|---|---|
| navigator.deviceMemory（浏览器 16）| ✅ 补 | 早期修复，未验证有效 |
| navigator.maxTouchPoints（浏览器 10）| ✅ 补 | 同上 |
| performance.now 冻结值 | ✅ 改真实递增时钟 | 同上 |
| **screen.availLeft / availTop** | ✅ 补（0）| **本次重点** |
| **document.location** | ✅ 补完整 location | **本次重点** |
| navigator.undefined（防御性读取）| 保留（SDK 自处理）| 非问题 |

**t 长度 940-1052 是 challenge 自然波动，不是修复效果（纠正误报）。**

## 判定中的实验

- LindaRogers7125（screen/doc 补全后）注册成功，存活待判
- 22:47（注册后 1h）为判定点
- 之前 vm 账号（无 screen/doc 补全）15min-1h 全死

## 结论与方向

- vm 能产 t/so；存活的关键是**补全 `_n` 读取的全部指纹值**（逐步诊断、逐步补）
- screen.availLeft/Top + document.location 是已知缺失，是否关键待存活判定
- 若活 → 继续补全指纹方向成立；若死 → 剩余缺口可能是 turnstile widget 执行（oneshot/WebAssembly）
