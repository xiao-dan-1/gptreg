# 资料/chatgpt_register 对照笔记（2026-07-12，P1 retest 期间）

来源：`资料/chatgpt_register.zip` → `资料/chatgpt_register/`
文件：
- `chatgpt_register.py` — 邮箱 OTP 注册编排
- `sentinel_token.py` — 纯算法 PoW + Node VM 产 t/so
- `gen_token_jsdom.js` — jsdom 跑 SDK 内部函数
- `cpa_codex_oauth.py` — Codex OAuth / 邮箱取码
- `input_runtime.json` — 一次 runtime 输入样例

## 1. 他们做对了什么（与我们结论同向）

### 1.1 双头语义正确
```python
headers["openai-sentinel-token"] = json.dumps(token)      # {p,t?,c,id,flow}
headers["openai-sentinel-so-token"] = json.dumps(so_token) # {so,c,id,flow}
```
与 Jennifer / 我们 P0 结论一致：**so 不在 token JSON 里，是独立 header**。

### 1.2 so 入口命名正确
- `get_so_token()` / `sessionObserverToken` / `collector_dx`
- `gen_token_jsdom.js` 注释写明 SDK 暴露 `sessionObserverToken`，并 hook 内部 `Nt` 跑 SO VM

### 1.3 注册主链同构
`init(login_hint)` → OTP validate → about-you → `create_account(name,bday)` → OAuth callback  
OTP validate **不加** sentinel（create 才加双头）— 与我们「OTP=pow 轻量 / create 才重 so」方向一致。

### 1.4 PoW 算法同系
FNV-1a + 指纹数组 + requirements `gAAAAAC` + enforcement `gAAAAAB` + POST `sentinel/.../req`  
与 `gptreg/sentinel.py` SentinelPoW / starmiaoa 同族；指纹字段更细（25 项伪造 navigator/document keys）。

### 1.5 chatReq 缓存复用
`get_token` 与 `get_so_token` 共享 `_cached_chat_req` / `_cached_proof`，避免 create 时双次 req — 设计干净。

## 2. 硬限制 / 包装不完整（不能当即插即用）

| 点 | 事实 |
|----|------|
| `input_runtime.json` 的 chatReq | 只有 `persona/token/expire/turnstile/proofofwork`，**无 `so` / `collector_dx`** |
| `get_so_token` 条件 | `if not so_info.get("required"): return None` → **该样例路径必然无 so** |
| Node 脚本路径 | 代码写 `sentinel_vm/gen_token_jsdom.js`，包内却是根目录 `gen_token_jsdom.js` |
| SDK 路径 | **硬编码** `~/.codeium/windsurf/sentinel_sdk_full.js`（本机多半不存在） |
| jsdom SO | hook 内部 `_n`/`Nt`；我们此前 Node-only 对照 **token 真 / so 假或无** — jsdom 仍非真浏览器行为采集 |
| `run_session_observer_vm` 旧接口 | 直接 `return None` |
| 假 so 过滤 | 未见对 SyntaxError / `MDogU3ludGF4` 的硬丢弃策略 |

结论：**这是「算法逆向 + jsdom VM」路线，不是「真页 sessionObserver」路线。**  
他们的架构图对；**so 落地在本包样例与依赖上并不闭环**。

## 3. 与本仓库路径对照

| 维度 | 资料项目 | 本仓库（当前） |
|------|----------|----------------|
| create 默认 | 尝试 token + so | **pow**（t=""，通常无 so） |
| 真 so | Node jsdom + collector_dx（样例无 so 字段） | **真 Chrome `sessionObserverToken`**（P0/P0.5 已绿） |
| OTP sentinel | 无 | pow（固定） |
| 假 so | 未见硬过滤 | 丢弃 SyntaxError / MDog… |
| post_login | 未见 me/init/prepare | config 已开 |
| 存活证据 | 包内无 delayed retest | **P1 1+1 进行中** |

## 4. 可吸收 / 暂不吸收

### 可吸收（低风险、不改默认 pow）
1. **chatReq 级观测**：pow/browser 路径日志打印 `chatReq.so.required` / 是否有 `collector_dx`（解释为何后端有时不要求 so）。
2. **create 双头缓存语义文档化**：token 与 so 应共享同一次 challenge（我们 browser 路径已同页同次 eval）。
3. 指纹字段清单作 **研究对照**（勿盲替换当前可过 create 的 PoW）。

### 暂不吸收（缺证据 / 与 P1 冲突）
1. 整包替换 `SentinelPoW` → 其纯算法 provider。
2. 接 `gen_token_jsdom.js` 当 so 主源（路径残缺 + jsdom ≠ 真 so 已否证方向）。
3. create 强制 require so（A 已成功有 so；B 无 so 也能 create — 强制会放大 Leslie 类 `registration_disallowed` 噪声）。

### 若未来做「纯协议 so」再开的实验
仅当 P1 证明 browser so **显著**长活，且想去掉 Chrome 时：
- 用**真实** `req` 响应含 `so.required + collector_dx` 的样本；
- 真页 vs jsdom 同 device_id 对照 so 长度/存活；
- 单变量；假 so 过滤不关。

## 5. 一句话

资料项目 = **正确的协议图 + 半成品 so 实现**；  
我们 P0.5 = **已验证的真 so 产线**。  
P1 存活结果出来前，**以 browser opt-in 为准，不把资料代码并进主路径**。
