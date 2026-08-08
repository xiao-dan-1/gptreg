# OpenAI access_token 刷新机制研究 2026-08-08

## 结论: refresh_token 确实一般没有, 刷新靠 session_cookies + sessionToken

### token 体系实测
| 凭证 | 寿命 | 获取 | 状态 |
|---|---|---|---|
| access_token (JWT) | **10 天** | 注册时 /api/auth/session | ✅ 已存 accounts.jsonl |
| sessionToken (JWE) | **~3 个月** (expires 2026-11-06) | 注册时 /api/auth/session | ❌ **未存** |
| session_cookies | 未知(推测长寿命) | 注册时 | ✅ 已存 |
| refresh_token | **不存在** | — | OAuth 响应无此字段 |

### 刷新链(实证)
```
session_cookies (35个) ──或── sessionToken
        │  GET /api/auth/session
        ▼
新 access_token (10天) + 新 sessionToken (3个月)
```

- **用 session_cookies 重抓 session**: HTTP 200, 拿新 accessToken + sessionToken + expires 3个月
- 用旧 access_token 带头重抓: 只有 WARNING_BANNER, 无 accessToken(不是刷新方式)
- sessionToken 是 JWE(A256GCM 加密), 无法直接解码, 只能靠 cookies/session 端点交换

### OAuth refresh 端点
- `auth.openai.com/oauth/token`: **存在**(400 "Missing client_id"), 参数齐全可尝试
- `auth.openai.com/api/oauth/token`: 404 不存在

### 现有代码缺口
1. `fetch_session` 只存 access_token, **未存 sessionToken**(3个月寿命的刷新凭证被丢弃)
2. 注册成功落盘时 session 响应里有 sessionToken, 但 `_partial_record` 没保存
3. 没有"token 过期自动续期"机制——10 天后账号 access_token 过期, 需手动用 cookies 重抓

### 待验证
- access_token 真正过期后, session_cookies 能否换新(当前 token 10天未过期, 无法即时实测)
- session_cookies 的寿命(是否永久)
- /oauth/token 完整参数(client_id + refresh_token)能否换新

### 建议
1. **注册时存 sessionToken** — 落盘补 `session_token` 字段(3个月刷新凭证, 比 cookies 更可靠)
2. **新增续期工具** `capture/refresh_at.py` — 用 cookies/sessionToken 重抓 session, 更新 access_token + sessionToken 落盘
3. 账号过期前(8-9天)自动续期, 保持账号库存活
