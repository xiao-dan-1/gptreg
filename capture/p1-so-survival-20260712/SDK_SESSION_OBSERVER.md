# sdk.js 只读切片：sessionObserverToken 调用链（2026-07-12）

范围：`vendor/sentinel/sdk.js` 静态反混淆。**不 eval dx、不产 so、不改主路径。**

## 1. 公开 API 面

```js
SentinelSDK.init(flow)                 // Me
SentinelSDK.token(flow)                // Ie  → {p,t,c,id,flow}
SentinelSDK.sessionObserverToken(flow) // → {so,c,id,flow} | so | null
SentinelSDK.timing()
```

iframe 守卫：`le === true` 时三者抛  
`sessionObserverToken() / token() / init() should not be called from within an iframe.`

## 2. 每 flow 状态机（Map `pe`）

`we(flow)` 惰性创建：

```js
{
  cachedProof: null,
  cachedChatReq: null,
  lastFetchTime: 0,
  sessionObserverCollectorActive: false,
  cachedSOChatReq: null,   // 关键：给 sessionObserverToken 用的整包 challenge
}
```

## 3. sessionObserverToken(flow) — 已还原

导出体（sdk 末尾，字符串表 `Ue`/`Hn` 解完后）：

```js
async function sessionObserverToken(flow) {
  if (inIframe) throw new Error("sessionObserverToken() should not be called from within an iframe.");
  const state = pe.get(flow);
  if (!state) return null;

  const chatReq = state.cachedSOChatReq;
  state.cachedSOChatReq = null;
  state.sessionObserverCollectorActive = false;

  const soVal = await runSO(chatReq);  // 见下
  if (!soVal) return null;
  // 有 challenge.token 则包成 JSON 串；否则裸 so
  return chatReq?.token
    ? JSON.stringify({ so: soVal, c: chatReq.token, id: oaiDidFromCookie(), flow })
    : soVal;
}

async function runSO(chatReq) {
  const so = chatReq?.so ?? null;           // qt
  if (!chatReq || !so?.required || !so.snapshot_dx) return null;
  try {
    return await Nt(so.snapshot_dx);        // ★ 主路径：snapshot_dx
  } catch {
    return null;
  }
}
```

| 步骤 | 条件 | 结果 |
|------|------|------|
| 无 pe 状态 / 未先 token | `cachedSOChatReq` 空 | `null` |
| `so.required` 假或无 `snapshot_dx` | — | `null` |
| `Nt(snapshot_dx)` 抛错 | 假 DOM / 超时 | `null` |
| 成功 | 有 `chatReq.token` | `{so,c,id,flow}` JSON |
| 成功 | 无 token | 裸 so 字符串 |

`ve(obj, flow)`：补 `id`（cookie `oai-did` URL-decode）+ `flow`，再 `JSON.stringify`。

## 4. 谁写入 cachedSOChatReq？→ ke(flow, chatReq)

在 `token()` 拿到 challenge 后调用 `ke(flow, cachedChatReq)`：

```js
function ke(flow, chatReq) {
  const state = we(flow);
  if (state.sessionObserverCollectorActive) return; // 已激活则不再进

  const so = chatReq?.so;
  const ok =
    so?.required === true &&
    typeof so.collector_dx === "string" &&
    typeof so.snapshot_dx === "string";

  if (ok) {
    state.cachedSOChatReq = chatReq;
    state.sessionObserverCollectorActive = true;
    Ut(chatReq);  // 后台预热，见 §5
  } else {
    state.cachedSOChatReq = null;
    state.sessionObserverCollectorActive = false;
  }
}
```

→ **必须先走过会调用 ke 的 token/challenge 路径**，`sessionObserverToken` 才有料。  
我们 browser harvest：同页 `token()` 再 `sessionObserverToken()` —— 顺序正确。

## 5. collector_dx vs snapshot_dx（根因级）

| 字段 | 谁用 | 何时 | 函数 |
|------|------|------|------|
| **`so.snapshot_dx`** | **sessionObserverToken 主路径** | 同步 await | `Nt(snapshot_dx)` → 真正的 so 字符串 |
| **`so.collector_dx`** | 后台预热 | `ke` 成功后 `Ut` fire-and-forget | `Ut` → 校验 required+collector → 带 proof 跑 SO VM（`Et(collector_dx, proof)` 类） |
| **`turnstile.dx`** | token() 产 `t` | token 路径 | `Pn(chatReq, turnstile.dx)` |

`Ut` 摘要：

```js
function Ut(chatReq) {
  const so = chatReq.so;
  if (chatReq && so?.required && so.collector_dx)
    runSOVM(chatReq, so.collector_dx)  // 用 WeakMap 里 cachedProof
      .catch(() => {});
}
```

`Nt` = 包装的 SO 字节码 VM（`Tt(() => Et(dx))`），与 turnstile 的 `Pn` 是**另一套**寄存器机，共享「dx = b64 混淆载荷」形态，**不能当明文 JS eval**。

### 资料/chatgpt_register 的错位

`gen_token_jsdom.js`：

```js
Nt(chatReq.so.collector_dx)  // ← 他们测 SO 走的是 collector
```

而 SDK 真 API：

```js
Nt(e.snapshot_dx)            // ← sessionObserverToken 走 snapshot
```

| | 资料 jsdom | 真 SDK sessionObserverToken |
|--|------------|------------------------------|
| SO 输入 | **collector_dx** | **snapshot_dx** |
| 是否要先 token/ke | 弱（直接喂 chatReq） | **强依赖** cachedSOChatReq |
| 输出包装 | 自拼 t/so | `ve({so,c},flow)` 含 oai-did |

这解释了：即使 jsdom 跑通 collector，也**不等于**浏览器 `sessionObserverToken` 产物；我们 P0 真页路径才对齐官方 API。

## 6. token() 对照（为何 token JSON 永无 so）

```js
// Ie / token(flow) 核心
p = getEnforcementToken(cachedChatReq)           // PoW
t = turnstile.dx ? await Pn(chatReq, dx) : null  // 可空
return ve({ p, t, c: chatReq.token }, flow)      // 无 so 键
// 副作用：ke(flow, chatReq) 可能启动 SO 预热并缓存 challenge
```

→ **设计如此**：so 不在 token 返回值里；另口 `sessionObserverToken` 取。

## 7. 与本仓库接线

| 已做 | 对齐 |
|------|------|
| browser：同页 token → sessionObserverToken | ke 写入后再读 cachedSOChatReq |
| so_header = `{so,c,id,flow}` | 与 `ve` 形状一致 |
| 假 so 过滤 | Nt 失败/SyntaxError 仍丢 |
| pow 默认无 so | 不调 Nt；chatReq 观测只看 required |

| 不做 | 原因 |
|------|------|
| jsdom `Nt(collector_dx)` 当主源 | 错字段 + 非真 API |
| 纯协议 eval snapshot_dx | VM 依赖真页环境；P1 未证必要 |
| OTP 交 so | 产品策略；challenge 虽可能 so.required |

## 8. 一句话

**`sessionObserverToken` = 取 `token()` 副作用缓存的 challenge，await `Nt(so.snapshot_dx)`，再 `ve({so,c})`。  
`collector_dx` 只是 ke 触发的后台预热，不是 so 主输入。**

## 9. 产物 / 禁止

- 本文：只读结论  
- 禁止：把本切片变成伪造 so 的实现依据；禁止关假 so 过滤  
- 下一步可选：headed harvest 比 so 长度；post_login prepare 是否另一套 so（chat 域）
