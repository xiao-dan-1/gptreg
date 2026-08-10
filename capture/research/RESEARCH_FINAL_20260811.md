# 纯协议注册研究收尾报告（2026-08-11 凌晨）

> 研究目标：**OTP-only 无密码注册 → 补设密码 → 开 TOTP 2FA，全程无浏览器**
> 完整原始记录：`survey-open-source-202608.md`

---

## 一、研究结论总览

| 环节 | 结论 | 证据 |
|---|---|---|
| ① OTP-only 无密码注册 | ✅ **可行**（社区主流） | register_otp 多次成功 |
| ② 纯程序 so 建号（vm so） | ✅ **能过 create** | 3 次 health=ok，1-3s 无浏览器 |
| ③ **补设密码（add_password）** | ✅ **纯协议跑通！** | `post_login_add_password=true` 参数，端到端验证 |
| ④ 开 TOTP 2FA | ✅ **可行且无需密码** | register.enable_totp 实测通过 |
| ⑤ 账号存活 | ⚠️ browser so 长活，vm so 短命 | 见下 |

## 二之补：纯协议补密码（2026-08-11 突破）

**原始研究目标完整达成**：OTP-only 注册 → 补密码 → TOTP，全程无浏览器。
- 关键参数：chatgpt signin 带 `post_login_add_password=true`（UI 点 Add password 时 SPA 的请求，agent 逆向生产前端 JS 确认）
- 序列：`signin/openai?reauth=password&max_age=0&post_login_add_password=true` → email OTP validate → `POST auth.openai.com/api/accounts/password/add {"password"}` → **200**
- 端到端验证：CloudMail 账号最终产出 `email----password----2fa` 全凭据（password + totp_secret 落盘）
- chatgpt backend `/add_password` 405 是死端点；真实机制在 auth.openai.com

## 二、三大硬结论

### 1. so 纯程序获取（vm so）能建号、不能长活
- **受控 A/B**（同 Outlook + 同代理 + 同钉 IP，唯一变量=so 来源）：
  - **vm so**（行为字段全 null）：19.5-31min 全灭
  - **browser so**（真实行为字段）：**232min / 517min+ 存活**（ElizabethJames 活了 8.6h+，BrianBlake 复现 n=2）
- **根因**：服务端延迟批审识别"会话行为证据缺失/伪造"。空行为 so、合成 so、不发 so 全被标记；只有真实浏览器行为通过
- **turb-gpt 的 Node VM so**：与我们的 vm so 同源（都无行为事件模拟），大概率同样短命

### 2. add_password 纯协议补密码 —— ✅ 跑通（2026-08-11 突破）
- `chatgpt.com/backend-api/accounts/add_password` 是 **405 死端点**（chatgpt 侧突变端点不存在）
- **真实机制在 auth.openai.com**：`POST /api/accounts/password/add` + `{"password"}`，需要 signin 带 **`post_login_add_password=true`**（UI 点 Add password 时 SPA 的请求参数）
- 序列：`signin/openai?reauth=password&max_age=0&post_login_add_password=true` → email OTP validate → `password/add` → **200**
- **端到端验证**：CloudMail OTP-only 账号 → 补密码 → 产出 `email----password----2fa` 全凭据，全程无浏览器
- `add_password/eligibility=false` 不影响 auth.openai.com 的 password/add 流程
- 缺 `post_login_add_password=true` 时返回 `invalid_auth_step`（我们此前的困惑根因）

### 3. 代理 IP 不是即时打标，但账号短命与代理强相关
- 同 IP 连注册 6 次成功 5 次（反欺诈"4-5 次 no_perm"未复现）
- 但所有走 1024proxy 的账号（无论 so）7-31min 短命，指向延迟批审（Ban 层）
- browser so 在坏代理下也只多撑一倍（61.7min vs 30min）；干净代理 + browser so 才能长活（ElizabethJames 8.6h）

## 三、纯协议路线的最终形态

**可交付 = 无密码 + TOTP 账号**（社区认可的标准形态，gpt-free-register 同款）：
```
OTP-only 注册（pow OTP + vm so 建号，~30s 无浏览器）
→ 注册后立即 mfa/enroll + activate_enrollment（TOTP，无需 reauth！）
→ 落盘 totp_secret（无密码，TOTP 为第二因子）
```
**✅ 2026-08-11 实测通过**：新鲜注册 token 的 `mfa/enroll → 200`（拿到 secret），`activate_enrollment → 200 {"success":true}`。`recent_auth_required` 只卡陈旧 token，注册后立即开 TOTP 无需 reauth。
**已实现**：`register_otp.py` 新增 `register.enable_totp: true` 配置 → 注册后自动开 TOTP。
**注意**：无密码账号唯一恢复通道=注册邮箱；TOTP 丢失即锁号（secret 必须落盘）。

## 四、代码修复（5 个 bug + 别名策略，未提交）

1. `register_otp.py`：cloudmail/iCloud 误用 plus 别名 → 收码查主地址永远超时
2. `register_otp.py`：`used_cache.remember(identity,...)` 引用未定义变量 → 建号成功但落盘崩溃
3. `register_otp.py`：`otp_after` 在 signin 后抓 → <1s 到件的 OTP 被当旧件过滤
4. `icloud_xdauv.py`：`filter_recipient:True` 按主号过滤 → 别名收件 OTP 被漏掉
5. `icloud_xdauv.py`：`extract_otp` 未导入（NameError）
6. **别名策略统一**：仅 ms_oauth 用别名，iCloud/cloudmail/api 用主邮箱
7. `config.yaml.example`：补 register/mail 段 + 死配置清理（与 config.yaml 同步）

> 这些修复让 **Outlook + 别名 + xdauv + vm/browser so 全链路打通**（实测多次成功）。

## 五、研究脚本（capture/research/）

| 脚本 | 用途 |
|---|---|
| `probe_vm_so.py` | 诊断 vm so 产出质量（6 变体） |
| `check_vm_so_survival.py` | 存活复测（支持 --sid 钉 IP） |
| `probe_addpw_post.py` | 探测 add_password 端点方法 |
| `probe_ui_addpw2.py` | UI 补密码探测（渲染有坑） |
| `reauth_enroll_otp.py` | OTP-only reauth→TOTP（recent_auth 未完全解决） |
| `reauth_set_password.py` | OTP-only reauth→设密码（invalid_auth_step） |
| `probe_normal_login_elig.py` | 正常登录→eligibility 重查（证伪会话类型假说） |

## 六、下一步建议

1. **提交代码修复**（确定性成果，不依赖代理/账号）
2. **完善 OTP-only + TOTP 流程**：reauth 后 mfa/enroll 的 recent_auth_required 需解决（callback 会话建立完整）
3. **browser so 采集优化**：so 采集是长活硬需求，优化效率（SDK 缓存已省 15s；可考虑会话复用/并行）
4. **代理资产**：账号短命与代理强相关，需干净住宅 IP 段
5. **可选**：调研"注册时设密码"路线（主路线 batch_totp）作为对照，确认其账号的 add_password/change_password eligibility

---

**一句话总结**：纯协议能建号、能开 TOTP，但**不能补密码**（服务端把无密码账号导向 passkey）且 **vm so 不能长活**（真浏览器行为不可替代）。可交付 = 无密码 + TOTP 账号；补密码和长活都需真浏览器。
