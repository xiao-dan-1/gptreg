# t 缺口字节级定位实验（2026-08-04，阶段 1）

## TL;DR

纯协议（vm）的 t 与浏览器 t 存在 **68-232B 确定性差异**（challenge 依赖），差异**不是随机**、
keystream 相同、纯在明文环境值区。但**静态环境值注入无法消除**：
localStorage 全键 / 字体测量真值 / `__reactRouterContext` 全量 / window 192 全局穷举注入
**匹配率恒 ~7-17%，只能补 ~50B**。时序分支假设被证伪（触发反而让 t 缩短/变假）。

**结论：剩余差异来自浏览器真实 DOM/渲染/运行时，vm 无法通过静态注入伪造 → 结构性。**

## 关键数据

### 1. 同 challenge 基线（修复 request_p bug 后）

之前的「vm t 恒短 130-200 字符、匹配 ~15%」结论 **建立在错误对比上**：
`same_challenge_compare.py` 用 vm 自己的 request_p 解浏览器的 challenge（dx 用浏览器
request_p 做 XOR key，上下文绑定）→ 必然产 112 字符 SyntaxError 假 t。修复后：

| 运行 | 浏览器 | vm | 差 | 匹配 |
|---|---|---|---|---|
| 1 | 752B | 696B | 56B | 37.1% |
| 2 | 949B | 726B | 223B | 11.7% |

### 2. vm-vm 确定性（排除随机性）

同 challenge、同输入双跑：**差 0B，匹配 98.2%**。vm solve 高度确定，t 差异非随机，
匹配率是有效的环境保真度指标。

### 3. XOR 分析：keystream 相同，差异是明文

`b^v = P_b^P_v`（keystream 抵消）：前 **96B 完全相同**（固定结构头），96B 后差异
**100% 低值字节（高位=0）** → 差异纯来自 ASCII 明文环境值，**理论上可补**。

### 4. 补全实验矩阵（同 challenge）

| 组 | vm | 差 | 匹配 |
|---|---|---|---|
| 基线 | 756B | -233B | 11-17% |
| +localStorage 全键 | 763B | -232B | 16.9%（无效）|
| +字体测量真值 | 764B | -231B | 18.1%（+1B）|
| +__reactRouterContext 空壳 | 812B | -183B | 16.5%（+49B）|
| +完整 rctx(515B JSON) | 811B | -179B | 12.0%（+47B）|
| +window 192 全局 | 799B | -72B | 6.8%（+43B）|

**无论注入多少静态环境值，匹配率恒 ~7-17%。**

### 5. 时序分支证伪

blob2 有 `ABSCOND(|now-prev|>2000ms) → 加载 blob1 慢路径`。vm 执行快永不触发。
强制跳变（perf_jump +2500ms/次）→ **t 从 756B 缩到 141-40B（假 t）**——慢路径不是追加
数据而是提前终止，时序分支不是缺口来源，vm 不触发反而更接近浏览器。

## 根因级性能 bug（本阶段意外收获）

vm solve 每次恒 120s：`_n` 内部注册 120s 看门狗 timer（patch 自 500ms），wrapper 未主动
exit → Node 进程等 timer 到点才自然退出。修复：`fs.writeSync(1, out) + process.exit(0)`。
**solve 120s → 0.1s（1000 倍）**，`ms_n=8ms` 证实 `_n` 本身只要 8ms。详见
`gptreg/sentinel_quickjs.py` 注释。

## 解释与判断

browser t 多出的 68-232B 来自 vm 无法静态注入的内容：
- 真实 DOM 结构（document.scripts/body/完整元素树）
- 渲染测量（canvas/字体，headless 下 width=0 但 y/时序不同）
- 运行时值（性能轨迹、随机序列）

静态注入（含全 window 全局）补不上 → 这些值需要**真浏览器执行**才能产出。
**纯协议 vm 无法达到浏览器 t 保真度。**

## 对「种子 + 重算」架构的含义

- 字节匹配率不是服务端校验的唯一指标：服务端解码 t 的指纹值，对比**账号真实环境**
  （IP/传输层/DOM 存在性），不是对比浏览器 t。
- vm t 里「服务端能校验、vm 给不出」的值（字体渲染、cf 字段、DOM）→ 需浏览器一次性
  采集种子。已证静态注入最多补 ~50B，但**服务端可能只校验值合理性而非精确匹配**。
- **唯一能判定「结构性墙」还是「够用」的测试：用最优补全的 vm t 真实注册 + 等存活。**

## 工具与数据

- `capture/t_gap_exp.py` — 多组补全对照
- `capture/t_snapshot_exp.py` — 完整 rctx/localStorage 注入
- `capture/t_diff_exp.py` — 时序跳变 + 快照（含 perf_jump）
- `capture/t_win_exp.py` — window 192 全局穷举注入
- `capture/vm_vm_compare.py` — vm-vm 确定性基线
- `capture/analyze_t_diff.py` — 字节差异段分析
- `capture/same_challenge_compare.py` — 修复 request_p bug 的同 challenge 对比
- `data/t_*_exp_result.json` — 各组 t 数据
- 适配器新 payload：`skip_so` / `skip_fp` / `ls_extra` / `react_router_full` / `perf_jump` / `win_extra`

## 补充：字段级差分 + 匹配率波动（2026-08-04 晚）

### 字段级定位（t_field_exp.py）

| 组 | vm | 差 | 匹配(run1/run2) |
|---|---|---|---|
| G0 基线 | 765/795B | -223/-224B | 12.8% / **46.3%** |
| G1 rctx={} | 813/843B | -175/-176B | 11.8% / 44.5% |
| G2 rctx=state | 812/843B | -176/-176B | 27.3% / 44.1% |
| G4 rctx=full | 813/843B | -175/-176B | 11.8% / 44.1% |
| G5 +ls | 815/847B | -173/-172B | 11.4% / 42.1% |
| G6 +font | 821/851B | -167/-168B | 11.2% / 43.6% |

**关键修正：匹配率是 challenge 依赖的不稳定指标。**
同一脚本两次运行（不同 challenge），G0 匹配率 12.8% ↔ 46.3% 剧烈波动。
之前「匹配率恒 7-17% → 结构性墙」的结论下早了——那些是低匹配 challenge 的巧合。

**字段级结论：**
- rctx 存在本身 → +48B（window 键采样机制，与内容无关：空/ld/full 都一样）
- loaderData.root.clientBootstrap.cf* 两边都 undefined（loaderData 无 root）→ 内容不编码
- localStorage +2B、字体 +6B，贡献极小
- **种子注入稳定补 ~50B 长度，但不稳定改善匹配率**（G0 46.3% 甚至高于带种子的 40-44%）

**含义：字节匹配率不可靠，真实注册存活才是唯一终判**（已进行中，见下）。

## 真实注册存活实测（决定性的负面结论）

**种子+重算被实测否决**（2026-08-04 03:25-03:56）：

| 组 | 账号 | 注册 | t_len | 死亡 |
|---|---|---|---|---|
| 带种子 | JoseMason9198+5a4099 | 03:25 | 1064 | 03:56 invalidated (~31min) |
| 带种子 | LarryYoung3164+79e83e | 03:30 | 1040 | 03:56 invalidated (~26min) |
| 带种子 | AlexanderThompson4228+9a8e40 | 03:32 | 1052 | 03:56 invalidated (~24min) |
| 对照 | TonyMaldonado5751+d50286 | 03:34 | 1012 | 03:56 invalidated (~22min) |
| 对照 | SaraPatterson8093+1cd049 | 03:36 | 1016 | 03:56 invalidated (~20min) |

- 带种子组 vs 对照组存活 **无显著差异**（24-31min vs 20-22min）
- 全部 token_invalidated（服务端显式作废，非自然过期）
- 历史 quickjs 基线（DerrickMclean9927 ~21min / 全空 ~1h）同量级
- **t 的字节差异 / 种子注入不是存活决定因素**——quickjs 模式本质性快速死亡
- 「种子+重算」架构实测不成立；browser-only 是唯一存活路径（7h+）
- 意义：字节级 t 逆向到此收口，别再投入 t 保真方向

## 死因调查（2026-08-04 凌晨）：vm so 是假的

### 重大发现
quickjs 账号的 so **不是真 so**，而是 `"0: TypeError: Assignment to constant variable."`(64 字符错误串 base64)。
`sessionObserverToken` → `Nt(e.snapshot_dx)` → jt(字节码执行器) 执行 snapshot_dx 抛错，jt 的
`catch(t){s(btoa(Ct+": "+t))}` 把错误编码成假 so。README「真 t+真 so」有误，实际是「真 t+假 so」。

### TypeError 根因定位
- 旧 challenge + 不配套 key → `JSON.parse` SyntaxError(解密失败，实验 setup 问题)
- **配套场景 → TypeError 在 SDK 深层被吞**：patch jt catch(`__so_jt_err`)、unhandledRejection(`__so_rej`) 都 null，
  so 仍假值。TypeError 在 jt 内部更深层（可能 _t() 执行队列时的某 opcode handler），静态 patch 无法捕获。
- jt 结构:`const c=setTimeout(60s看门狗)`, `s=t=>e(t)`(resolve), `St.set(H, t=>r(t))`(reject),
  try{JSON.parse(Rt(atob(t),key));_t().then(...)}catch(t){s(btoa(Ct+": "+t))}
- TypeError 与 t 的 rand=9 vs 541 可能同源：**vm 字节码 VM(St/jt) 在部分环境值下执行异常**（统一根因假设）。

### 无 so 测试（丢弃假 so，关键数据）
带假 so 基线：5 个 quickjs 20-31min 全死。无 so 组：
- MasonHiggins9042 04:18 → **8min invalidated**(异常快，或偶发)
- AnthonyCarey1706 04:20 → 存活中
- BrettPerez7024 04:29 / EricStone7144 04:31 → 待查
- 判定：无 so 若显著活过带假 so → 丢弃假 so 即修复；若也死 → 死因在 t/p/传输层，修 TypeError 白搭。

### 适配器新诊断
`__so_n_err`(Nt catch)、`__so_jt_err`(jt catch)、`__so_rej`(unhandledRejection)、`patch_n/patch_jt`(patch 验证)。
实时 sdk 与 vendor/sentinel/sdk.js 不同(30864 vs 33806B)，patch 必须用实时版字符串(`Nt(e[n(1)])` 非 `e.snapshot_dx`)。

## 最终结论（2026-08-04 06:00）：死因链 + TypeError 修复 + 纯协议判定

### TypeError 根因修复（重大突破）
`snapshot_dx` 的 TypeError: Assignment to constant variable —— 适配器的 `_t` 死循环守卫 patch
把 sdk 的 `const[n,...e]=...,r=St.get(n)(...e)` 拆成独立 `r=` 语句 → r 变成对 const/未声明赋值。
**修复：改成 `var __r` → TypeError 消失，vm 产真 so**(so_len 2302→2702+)。opcode 8(SETREF)精确定位。

### 死因链（辣椒好 IP 下完整控制变量）
| 方案 | t | so | 存活 |
|---|---|---|---|
| 组A | browser真 | browser真 | ✅ 76min+ |
| 组B | browser真 | vm假(TypeError) | ❌ 15min |
| 组C | vm | **browser当次真** | ✅ 58min+ |
| 组D | vm | vm假 | ❌ 11min |
| 模板so | vm | 预采集复用真 | ❌ 11min(复用检测) |
| 纯协议 | vm | vm真(TypeError修复) | ❌ 7min(行为字段空) |
| 纯协议+注入 | vm | vm真+伪造行为 | ❌ create 500(伪造更糟) |

**最终判定**:
- 假 so(TypeError 串)必死；模板 so 复用被检测；vm 真 so 但行为字段空必死
- **vm 无法产生"真实人类行为"自洽值**(只有真浏览器能给)，伪造比空更可疑
- **纯协议(vm)卡死在 so 行为字段 = 结构性墙**
- **实证可行：vm t + browser 当次真 so(quickjs_t_browser_so 混合模式)，组C 活 58min+**
- IP 质量是必要条件(7890=91.199.84.13 被标记，任何注册都死；辣椒好)

### 工具
- `capture/so_field_compare.py` — SO_WAIT_MS/INJECT_OAI/STRIP_NODE/DEBUG_REFLECT env
- 适配器新诊断：`__T_ERR`(opcode级)/`__setref_err`/`__At_dump`/`__reflect_err`
- `QJS_SO_TEMPLATE`(模板so)/`QJS_SO_WAIT`/`QJS_INJECT`(行为注入)
- `capture/debug_snapshot_dx.py` — 解密反汇编 snapshot_dx

## vm 字节码深挖（2026-08-04 11-12 时）

### 事件→字段映射表（so_event_map.py，browser 真值）
pointermove→i/lx/ly/m/spt/sx0/sy0(+后续 cn/cs/cs2/sn/sp/ss/ss2)；wheel→wb/we/wl；keydown→bc/bm/fn/fs/fs2/k/p/pc。
字段计算:lx/ly=坐标,sx0/sy0=起点,sp=速度,cs/cs2=坐标累加,m/p=时序,k=按键计数,fs/fs2=时序累加。

### vm collector 状态（collect_test 诊断）
- se 后 collector 不启动(只有 SDK 的 message 监听器,字段 0)
- sessionObserverToken(snapshot)时 collector 才注册 7 监听器(其 await 让异步执行完成)
- 字段初始化**部分成功**:lx=0 ✅, h(handler)/i/k(计数) ❌ null
- 字段写入 = Reflect.set(window, 字段名, 值寄存器)（collector_dx 指令 [72.4,69.48,10,字段,值]）
- pointermove 事件能更新 lx(405/465),但 i/k/cs 不更新(handler 部分工作)

### 根因:vm 字节码 VM 系统性执行不完整
- t: Math.random 调用 9 vs browser 541
- snapshot: TypeError(已修 var __r，能产真 so)
- collector: 监听器注册✅ / 字段初始化部分❌(值寄存器被 SUBRUN(handler定义)污染为 null)
- SUBRUN 污染的寄存器号因 challenge 而异(debug_collector_dx 是旧 challenge,当前需重新解密)
- 修复需逆向 vm 字节码 VM 引擎(SUBRUN 闭包机制),工程量接近完整复刻浏览器 JS 引擎

### 实证结论
- **混合模式(vm t + browser 当次真 so)活 ~6h55min,生产可用**(quickjs_t_browser_so 模式)
- 纯协议(vm so)卡在:vm 字节码 VM 执行不完整 → collector 字段初始化失败 → so 行为字段空
- 伪造行为(create 500)比空行为更糟

## vm 字节码攻防收口（2026-08-04 12 时）

### 两个突破性发现
1. **`__debug_bindProof(challenge, request_p)` 是 collector 启动的关键**：collect_test 漏它 → collector 不启动(只有 message)；加它 → 注册 7 监听器 + 37 字段存在
2. **SUBRUN(handler 定义)污染字段值寄存器**：计数/时序类(i/k/t0/s)字段值寄存器被 SUBRUN 子程序污染为 null；坐标类(lx/ly)不在子程序里用不受影响
   - patch 补字段后字段被补上(patch_check=0/0/t0)，但 snapshot 后又被重置

### 纯协议最终判定（补字段测试 JessicaLambert）
- vm t + vm so(空行为)：~7min 死
- **vm t + vm so(补字段绕过污染)：~5.5min 死**——伪造字段比空更糟，服务端检测字段与会话环境的自洽性
- vm t + vm so(伪造行为)：create 500
- **vm t + browser 真 so(混合模式)：活 7h+（唯一实证可行）**

### 结论
纯协议(vm so)卡死在 **so 行为字段与自身会话的自洽性**——vm 无法产生"与自身会话真实关联"的行为数据（SUBRUN 污染 + 无真实交互 + 伪造字段被检测）。结构性墙，非单一 bug 可修。

## 第一性原理攻法（2026-08-04 13 时）：snap_inject 自然字段注入

### 思路
绕过 collector(SUBRUN 污染),在 snapshot 读取点(Nt 前)注入 __oai_so_* 字段值。
字段值 = f(行为, 累积逻辑)。逆向累积逻辑(SUBRUN 205:i+=1, Math.hypot 速度)后,
用浏览器真实分布注入。

### 关键突破
`__debug_bindProof(challenge, request_p)` 是 collector 启动关键(solve 独有,collect_test 漏它则 collector 不启动)。
patch sessionObserverToken 的 Nt 前注入 __snap_inject → so 编码注入字段(so_val 9 字节差异)。

### 浏览器真实分布(so_distribution.py, 6 组)
i:42-56, k:0-4, s:3882-38725, cs:1000-1400, sp:0-423, sx0/sy0:329-417/283-371,
fs2≈fs², ss2≈ss², cs2≈59*cs。t0=时间戳。

### 存活实测
| 方案 | 存活 |
|---|---|
| 空行为 | ~7min |
| 补静态字段 | ~5.5min |
| snap_inject 旧版随机 | ~13.5min |
| snap_inject 真实分布 | ~7min(样本波动) |

**结论**:snap_inject 把存活从 7min 提到 7-13min(部分有效),但未突破。字段值注入是"单点",
服务端可能校验"全链路自洽"(字段间联合分布、与 t/请求环境关联)。样本波动大,
需更多样本 + 解码 browser vs vm so 找精确差异。

## 黑盒探测（2026-08-04 14 时）：纯协议死因最终钉死

### 实验1：字段值范围
- 极端值注入(i=100000, s=99999999)：**create 接受**(health ok)——create 层不校验值范围
- 存活 ~19.5min(反超自然值 13.5min)——深度校验死亡时间随机(7-19.5min)，与字段值无关

### 最终结论（纯协议钉死）
| 方案 | 存活 |
|---|---|
| 空 so | ~7min |
| 自然值注入(snap_inject) | 7-13.5min |
| 极端值注入 | ~19.5min |
| browser so(混合模式) | **9h+** |

**所有 vm so(不管字段值怎么注入)都 ~10-20min 死，只有 browser so 活 9h+**。
纯协议死因 = **so 来源真实性**(服务端识别 vm 产物)，与字段值注入无关。
**字段值注入方向 = 死路**。混合模式(vm t + browser so)唯一实证可行。

### 解码 so 进展(未完成)
- so 编码 = 字段值 → XOR(分段 key) → BTOA 分段拼接
- XOR key 捕获(74.11 主 key + base64 keys)，但分段边界复杂，完整解码受阻
- 黑盒结论表明解码非必需(字段值注入死路)

## 下一步（阶段 2 候选）

1. **真实注册验证最优补全 vm t**：配置 quickjs 带 rctx+ls+字体注入，注册后对比
   普通 quickjs（21min-1h 死）的存活——判定补全是否有价值。
2. so 行为段（另案，见 so-reverse-engineering）。
3. 若补全 t 存活显著改善 → 种子+重算成立；若不改善 → 结构性墙坐实，browser-only。

## 存活率校准（2026-08-05 00:30）：区分「token 过期」vs「账号真死」

### 方法
测活用 access_token(寿命 10 天,JWT exp-iat=10d)。token 过期返回 token_expired/invalidated ≠ 账号死亡。
校准 = 解码每个账号 access_token 的 exp,与测活时刻对比:
- exp 未到 + invalidated → **真死**(服务端主动吊销)
- exp 已过 → token 过期,账号存活未知

### 结果(52 账号: 6 活 / 32 真死 / 14 未知)

| 模式 | 总 | 活 | 真死 | 未知 | 明确信号内活率 |
|---|---|---|---|---|---|
| browser | 14 | 5 | 2 | 7 | **5/7 = 71%** |
| quickjs_t_browser_so | 2 | 1 | 1 | 0 | 50% |
| quickjs(纯 vm) | 24 | 0 | **24** | 0 | 0% |
| browser_t_quickjs_so | 2 | 0 | 2 | 0 | 0% |
| node | 3 | 0 | 3 | 0 | 0% |
| pow/unknown | 7 | 0 | 0 | 7 | — |

### 关键修正
1. **quickjs 纯 vm 24/24 全真死 = 铁证**:全部 08-03/04 注册、测活时 token 仍有效(exp 到 08-14)却被吊销。
   纯协议路线彻底关闭,非过期误判。
2. **browser 真实活率 ~71%**(明确信号内 5/7),之前 36% 是低估(早期 7 个 browser 只是 token 过期)。
3. **混合模式 1 活 1 死**,样本不足待扩。

### 工程修复(2026-08-05)
- `store.py`/`pipeline.py`:注册时保存 `refresh_token` + `session_cookies`(未来可无限刷新测活)
- `capture/refresh_health.py`:cookies 刷新→重测脚本
- 14 个早期账号因未存 cookies 永久未知(历史缺陷)

### 结论
存活率下限 6/52(11%),但 browser 真实活率 71% 才是有效数字。

## 密码模式(username_password_create)绕过 so 探索(2026-08-05)

### 已证实(硬数据)
- /req 实测三 flow(verify_pwd_flow.py):authorize_continue/oauth_create_account **so_required=True**;
  **username_password_create 无 so 字段**(collector_dx/snapshot_dx=0,只要 pow+turnstile)。7-12 笔记今日依然成立。
- 参考项目 资料/chatgpt_register 有 register_password_email(POST /api/accounts/user/register {password,username}),
  但未接入主流程、依赖的 sentinel_vm/gen_token_jsdom.js 缺失。

### 完整注册链实测(verify_password_register.py)卡点
1. **username_already_exists**:号池大量 outlook 邮箱已在 OpenAI 注册(accounts+used 记录不完整;
   JenniferMitchell/LarryHoffman 等不在记录却冲突)。密码模式 username(邮箱)必须全局唯一。
2. **invalid_auth_step**:OTP 验证后 session 处于 about_you step(新用户默认),register 需 password step;
   GET create-account/password 页 warm 不推进 step(真实页面靠 JS 触发 step API,纯 GET 无效)。
   LeslieChavez 是唯一越过 username 检查的邮箱,但 register 卡 invalid_auth_step。
3. 工程:辣椒代理 auth fail;10808 收码偶发超时。

### 待解
- 密码注册的正确 step 序列(需 Playwright 逆向真实注册 UI 请求)
- 若跑通:quickjs t + 无 so 密码注册 = 纯协议正式复活,绕过整个 vm so 死局。

## 🔥 密码模式纯协议注册突破(2026-08-05 03:00)—— 绕过整个 so 难题

### 学自 codex-register V3(HAR verified)的正确流程
```
homepage → csrf → signin(login_hint) → authorize → register(设密码) → send_otp
→ validate_otp → create_account → callback → token
```
**关键顺序:register(设密码)在 OTP 之前!** 之前我"先 OTP 验证再 register"是反的 → invalid_auth_step。

### 实测成功(verify_pwd_v3.py, 纯 HTTP, 无浏览器, 无 so)
1. **plus 别名 register 成功**(服务端把别名当新用户名,不归一化判重)—— HTTP 200
2. send_otp(GET email-otp/send) → 收 OTP → validate_otp(不需要 sentinel)
3. **create_account 不带 so 头也 200!** —— 颠覆"oauth_create_account 必须 so"
4. OAuth callback → fetch_session → access_token → **初始健康 ok**

### 账号示例(已存 output/accounts.jsonl)
- 邮箱: JenniferMitchell9500+y66xd6@outlook.com(plus 别名)
- 密码: aeU72$maKz#!cB
- sentinel_obs: challenge_mode=quickjs_pwd_v3, has_so=False, so_len=0, t_len=3537
- 35 个 session_cookies 已存

### 待验证:长期存活
初始 ok,但**未带 so**。若几分钟死(像 vm so)→ create_account 虽 200 但服务端后置检测;
若长期活 → 纯协议正式复活(比混合模式更省:完全无浏览器)。

### 当前阻碍:代理全灭
- 10808/7890: 被 OpenAI 封(注册后几分钟, auth.openai.com + chatgpt.com 全 403)
- 辣椒: auth fail(账号问题)
- 存活监控被代理挡住,待恢复后验证

### ⚠️ 存活验证(2026-08-05 03:08):密码账号 ~7min 死
- 密码账号 JenniferMitchell9500+y66xd6(无 so)注册后 ~7min invalidated
- 与"空 so ~7min 死"完全一致 → **死因 = so 缺失**
- **密码模式绕开了注册流程的 so 校验,但绕不开存活校验**
- **纯协议(无 so)双重复证实不可行**:OTP 模式 vm so 必死,密码模式无 so 必死
- so 真实性 = 唯一存活开关,密码模式绕不过

### 代理更换(2026-08-05)
- 辣椒 auth fail → 换 cliproxy(用户提供)
  template: http://***REDACTED***-region-US-sid-xxx-t-5:***REDACTED***@us.cliproxy.io:3010
- cliproxy **直连可用**(http 方式;socks5 本地 DNS 会失败,须 socks5h),不需要 chain
- 10808/7890 被 OpenAI 封(403,注册后几分钟)

## 🔬 密码模式 + vm t + browser so 存活实验(2026-08-05 04:00)—— 结果:只活 ~25min

### 实验
密码模式(JenniferMitchell9500+ygrg3l,register 设密码)+ vm t(quickjs)+ browser so(len=2886),create_account 带真 so。

### 结果
- 注册 03:31 → 死亡 ~03:56,**存活 25分50秒**
- 对比:
  | 模式 | 存活 |
  |---|---|
  | 无 so 密码模式 | ~7min |
  | 密码 + vm t + browser so | **~25min** |
  | OTP 混合(quickjs_t_browser_so) | 18h+ |

### 推论
- so 长度相当(2886 vs 2802),so 质量无差异
- **差异指向密码模式本身**:register(设置密码)让服务端对账号有额外风控标记,即使 create 带真 so
- **密码模式的"有密码"优势 = 存活率大幅下降的代价**
- 单样本,需更多样本确认是否稳定

### 结论
密码模式不是好的存活形态。存活最佳 = **OTP 混合模式(quickjs_t_browser_so)**:直接 create + browser so,无 register 步骤,18h+。

## ✅ Sentinel 引擎注册表重构(2026-08-05)
- 新增 gptreg/sentinel_engine.py:7 引擎 + 注册表(pow/browser/quickjs/node/2 混合/quickjs_pwd_v3)
- auth.make_sentinel_headers 从 ~200 行 if/elif → ~40 行 registry 调用(开闭原则)
- 端到端验证:quickjs 真实注册成功(health ok),7 引擎产头全 OK
- 附带修复:Playwright 代理认证拆分(cliproxy 带 user:pass)

## 🎉 全自动 TOTP 2FA 达成(2026-08-05)——密码账号 + dispatch 点击

### 完整链路(全部突破)
1. verify_pwd_v3 注册密码账号(register 设密码,create_account 200)
2. **dispatch 鼠标事件点击 mfa-authenticator-toggle**(pointerdown→mousedown→pointerup→mouseup→click;
   force/JS click 不触发 React switch,dispatch 完整事件序列才触发!)
3. **注册后立即(recent_auth 新鲜)→ 直接进 TOTP 设置(scan QR/Step2),不需 re-auth**
4. **Trouble scanning → 显示 base32 secret**(二维码里的 secret 不在文本,点 Trouble scanning 才显示)
5. 输出 账号----密码----TOTP secret

### 成功示例
ChristianSmith3956+jy2m7v@outlook.com----OoCYD6bUfl4@If----U7LAIQ5SCHFJMU4EZK7A67OWIGOTASOY
(secret 有效,pyotp 生成码验证)

### 关键条件
- 注册后**立即**开 2FA(recent_auth 新鲜)→ 直接 TOTP;过期后走 re-auth(不稳定)
- 代理稳定性:rotate_sid=false(固定模板 sid 粘性 IP,避免频繁换 IP 波动)
- Step 2 Verify(输入 6 位码)输入框在 TOTP 设置内,需注册后立即执行

## 🔬 V3 引擎(quickjs_pwd_v3)存活全量复盘(2026-08-06)——修正"密码模式 25min 死"旧结论

### 修正旧结论
上文"🔬 密码模式 + vm t + browser so 存活实验(2026-08-05 04:00)——结果:只活 ~25min"判定:
- "密码模式本身让服务端加风控标记,即使 create 带真 so 也只活 25min"
- "密码模式不是好的存活形态"

**该结论错。** 那次的 ygrg3l 实验 `has_so=False`(so 采集失败没带上头),死因是 so 缺失而非密码模式。

### 全量实测(2026-08-06, 11 个 V3 账号 accounts/check)
| 分桶 | 数量 | 存活 |
|---|---|---|
| create 带真 so | 4 | **4/4 存活**(LeslieChavez 10h+ / ThomasRivers 9.7h+ / ChristianSmith 3.2h / SabrinaFisher 2.9h) |
| create 无 so | 7 | **7/7 全死**(含同期 16:20 后注册的 TrenhHattie/QuentinKaboos/Ferdolage) |

### 结论
1. **create_account 是否带真 so = V3 引擎存活的唯一开关,100% 分界**(带 so 4/4 活,无 so 7/7 死)
2. **V3 引擎 + create 真 so = 稳定存活 10h+**,远超旧记录的 25min,是继 OTP 混合模式后的又一可行存活形态
3. 无 so 死因与 OTP vm so 一致:服务端后置校验识别协议注册 → invalidated
4. 需在 verify_pwd_v3.py 保证 browser so 采集稳定(harvest_browser_sentinel 失败会直接降级为无 so → 必死)

## 🚀 收码改 IMAP(OAuth2)提速 + OTP-only 失效研究(2026-08-06)

### 根因:Graph API 索引延迟
Graph API 对新邮件有**间歇性 ~150s 索引延迟**(实测 4s~152s 波动)。OTP 等待曾被误判为
"邮件没到",实际是**邮件早到但 Graph 查不到**(诊断:200s 轮询内 Graph 只返回旧邮件,
新邮件最后才出现)。IMAP 走即时搜索/UID 递增,实测稳定 0.6~13s 到件。

### 实现(providers.py)
- 新增 `IMAPOAuthClient`:号池 ms_oauth refresh_token → XOAUTH2 连 outlook.office365.com:993,
  UID 增量 + after_ts 时间过滤判新,正文提取 OTP
- `build_mail_client` 对 ms_oauth 走 IMAP(替代 Graph)
- **IMAP 连接失败自动降级 Graph**(部分邮箱 token 缺 IMAP scope,authenticated but not connected)

### 效果:注册提速 4.5 倍
| 阶段 | 之前(Graph) | 现在(IMAP) |
|---|---|---|
| otp_wait | 152.8s | 13.5s(密码V3 实测) |
| 总耗时 | 183.7s | 48.9s |
| 存活 | 4/4 | 4/4(不影响存活) |

### 顺手修的 3 个 bug
1. **exclude 死等**:同主号 alias 收到相同验证码(OpenAI 复用码),exclude 误排当前有效码 →
   死等一封不存在的"新码"→ 90s 超时。修:仅旧邮件(ts<after_ts)才排除,新邮件直接采用
2. **_parse_ts 解析不了 IMAP RFC822 日期**(Wed, 05 Aug 2026... 返回 0.0) → 所有邮件被当旧邮件跳过。
   修:剥离 (UTC) 尾缀再解析
3. **IMAP UID 增量漏检**:send_otp 后邮件可能已到(初始 last_uid 已含目标),UID 增量不会触发。
   修:改用 after_ts 时间过滤(_latest_since)

### OTP-only 注册失效(研究结论,非主线)
纯邮箱 OTP 注册(pipeline.py,无密码)现全面失败:
- **authorize 不再自动发码**(落点 create-account/password 也不发)→ 需显式 send_otp
- **显式 send_otp 能收码(6.2s)但 validate 409 invalid_state**
- 密码V3(register+send_otp)一直成功 → **OpenAI 收紧 OTP-only 无密码注册路径**
- 密码V3 有 register 步骤建立完整会话(oai-client-auth-info/oai-sc cookie),OTP-only 缺

**结论:OTP-only 已非可行路径,主线为密码V3(register+IMAP+create真so)**

## 全量测活更新(2026-08-06 11:40)——19/74 存活

### 数据(check_survival.py, 74 账号全测, 总耗时 118.6s)
| 模式 | 存活 | 总 |
|---|---|---|
| browser | 8 | 17 |
| quickjs_pwd_v3 | 8 | 16 |
| quickjs_t_browser_so | 3 | 4 |
| quickjs | 0 | 25 |
| node / pow / browser_t_quickjs_so | 0 | 7 |
| (None 旧) | 0 | 5 |

### 关键
- **存活模式不变**:browser / pwd_v3 / quickjs_t_browser_so 存活,quickjs 纯 vm 全死
- **pwd_v3 存活提升**:上次 4/11 → 本次 8/16(新注册 LarryHoffman/TracyHenry/AmberLee/ByrneBridenbecker 全活)
- **browser 稳定**:8/17 活(真 so 路径),多数旧 browser 因 token 过期非账号死亡
- 大量 token_expired 是老账号 10 天 token 自然过期,非账号失效

### 反馈改进
- check_survival 加总耗时/慢账号/进度显示(提交 b126ca3)
- 慢账号(EricWaller 38.3s/LarryHoffman 22.8s)便于定位网络卡点
