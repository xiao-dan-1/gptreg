# Browser SO Harvest

- time: 2026-07-12T22:18:52
- flow: `oauth_create_account`
- proxy: `http://127.0.0.1:7890`
- headless: True
- ok: 2/2
- has_so: 2/2
- t_syntaxerror: 0/2

| # | ok | has_sdk | has_so | so_len | so_header_len | t_len | t_syntax | page | error |
|---|----|---------|--------|--------|---------------|-------|----------|------|-------|
| 1 | True | True | True | 480 | 2654 | 1316 | False | https://auth.openai.com/about-you |  |
| 2 | True | True | True | 464 | 2702 | 1300 | False | https://auth.openai.com/about-you |  |

## 判读

1. has_so>=1 → P0 技术通路成立，可接协议 opt-in + 新根邮箱 P1。
2. 全无 so 但 t 真 → 仍缺 sessionObserver/登录态行为；升级：登录后 about-you 再采。
3. SDK 未暴露 → CSP/页不对；换 page 或 headed 人工确认。
4. 禁止：假 so、关过滤、无 so 宣称存活已解。

full: `D:\home\06_projects\GPT协议注册机\capture\browser-so-harvest-20260712-221822\harvest.json`
