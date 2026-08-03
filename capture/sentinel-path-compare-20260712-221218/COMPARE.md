# Sentinel 三路径对照（零耗号）

- time: 2026-07-12T22:12:24
- device_id: `ece6612f-78df-40f9-901d-0ae18bf5d00d`
- flow: `oauth_create_account`
- proxy: `http://region-USsid-wL2yQgzX@us.lajiaohttp.net:2000 via-chain`
- **注册主路径默认: `pow`（纯 Python）**

| mode | ok | keys | has_so | so_len | so_header_len | t_len | t_empty | t_is_syntaxerror | p_len | c_len | elapsed_s | error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pow | True | p,t,c,id,flow | False | 0 | 0 | 0 | True | False | 557 | 2124 | 3.307 |  |
| file | True | p,t,c,id,flow | False | 0 | 0 | 132 | False | True | 625 | 2124 | 1.404 |  |
| url | True | p,t,c,id,flow | False | 0 | 0 | 832 | False | False | 609 | 1764 | 1.079 |  |

## 判读规则

1. `pow`：当前 `auth.make_sentinel_headers` 实际路径；预期 `t` 空、`has_so=false`。
2. `file`：旧 Node；常见 `t`=SyntaxError、`has_so=false`。
3. `url`：k12 闭环；`t` 可非 SyntaxError，但仍常 `has_so=false`。
4. **任何路径 has_so=true 且 so 非假值 → 值得立刻接 P1 存活实验。**
5. 三条全无 so → 确认 P0：浏览器真页产 so，勿再盲改 PoW/伪造 so。

## 原始 JSON

`D:\home\06_projects\GPT协议注册机\capture\sentinel-path-compare-20260712-221218\compare.json`
