# 纯协议研究交接（2026-08-11）

> 目的：下次继续研究时快速了解现状、待办、如何继续。
> 完整研究记录：`survey-open-source-202608.md`（全部验证）+ `RESEARCH_FINAL_20260811.md`（收尾报告）

---

## 一、已完成（8 个 commit，工作树干净）

| Commit | 内容 |
|---|---|
| fbec027 | 收码链路 5 bug 修复 + OTP-only 自动开 TOTP |
| a9962a5 | **纯协议补密码突破**（`post_login_add_password=true`） |
| cb280e9 | register 集成补密码（`enable_password` 单命令产出 password+2fa） |
| 4b90821 | README 纯协议路线说明 |
| 11dc613 | 纯协议账号登录验证（password+TOTP 凭据 200） |
| e0df59c | 无密码 OTP 登录测试（validate 403） |
| 1205506 | OAuth consent 解（GET-follow）+ signin/openai 403 根因 |
| fce7aa7 | 登录链结论（vm-so 账号 7-30min 死，恢复价值低） |

## 二、核心成果（原始研究目标达成）

**纯协议注册 + 补密码 + TOTP，全程无浏览器，单命令产出 `email----password----2fa`**：
```bash
python main.py register -n N --sentinel-source quickjs --pool <号池>
```
config 需开：
```yaml
register:
  enable_totp: true
  enable_password: true
  default_password: "统一密码"
```

**关键参数**：补密码走 `auth.openai.com/api/accounts/password/add`，signin 必须带 `post_login_add_password=true`（缺则 invalid_auth_step）。

## 三、研究结论速览

| 主题 | 结论 |
|---|---|
| vm so 纯程序获取 | ✅ 能建号（~26s 无浏览器），但**账号 7-30min 死**（短活） |
| browser so | ✅ 长活（8.6h+），so 采集是长活硬需求 |
| add_password | ✅ 纯协议可补（post_login_add_password=true） |
| TOTP | ✅ 纯协议可开（enroll→activate，无需 reauth） |
| 登录 token 获取链 | ⚠️ 已记录为可选后续（vm-so 短活账号恢复价值低） |

## 四、待办（下次可选）

1. **browser so 采集优化落地**（主路线提速 ~12s→2-4s/账号）：
   - 常驻浏览器复用（klsf 模式，按代理分池 + 换 oai-did cookie + reload frame_url）
   - 需验证 frame_url 直连 vs about-you 的 so 行为字段一致性
   - 方案见 survey 的"browser so 采集优化调研"节
2. **登录 token 链闭环**（✅ 已研究完，2026-08-12）：
   - **Codex OAuth 拿 refresh_token = 强制手机验证**（页面 "Phone number required"，无 skip；10808/1024proxy 都触发）→ 注册机无手机账号**不可行**（除非接码，get-rt.js 用 smscode 等）
   - **chatgpt 客户端 raw OAuth**：不强制手机，能拿 code 但 /oauth/token 302 token_exchange_user_error（chatgpt 服务端持 client_secret）→ 也不可闭环
   - ✅ **续命已解决**：chatgpt 原生 signin 链（**去 `ext-passkey-client-capabilities=1111`**）→ password+TOTP 重登 → 新 access_token。已落地 `python main.py relogin --email <完整邮箱>`（gptreg/commands/relogin.py，实证 me=200）
3. **"1 Outlook = 5 别名"容量实测**：号池补充后测别名数量上限
4. **主工作树同步**：工作树 config.yaml 改了 `max_wait 200 / use_xdauv true / enable_totp true / enable_password true`，记得同步主工作树

## 五、关键文件/参考

- 研究脚本（`capture/research/`）：`reauth_set_password.py`（补密码）、`login_2fa_pkce.py`（登录链）、`check_vm_so_survival.py`（存活测）、`probe_vm_so.py`（vm so 诊断）等
- 参考实现：`C:\Users\xiaodan\AppData\Local\Temp\oauth_research\gpt\account-manager\node\get-rt.js`（Codex 客户端登录链）、`D:\tmp\turbrepo\`（turb-gpt 源码）
- 官方前端逆向：`data/research_js/`（chatgpt 生产 JS，含 post_login_add_password 等参数）

## 六、账号池状态（研究消耗）

- Outlook 池 19 个全测过（token 正常，3 个 MS 滥用除外），根邮箱被 OpenAI 记住
- CloudMail a8f2 域可动态生成（`generate_email`），只用于注册/收码验证，不用于存活
- 存活研究用 Outlook 别名（1 Outlook = 5 别名）
