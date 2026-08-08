# 2FA 注册测试记录 2026-08-08

## 成功案例（干净主号 + IMAP 可用）

- EricPatel3950+svrgm9@outlook.com（别名）— 总耗时 42.7s，TOTP 完整落盘
- 6 段归因：signin+register=11.1s / OTP等待=9.2s / create段=14.8s / session=3.1s / health=0.8s / enroll=3.7s
- create 并行：t=1.8s + so=10.3s（nav=3.25s sdk=4.24s token=10.08s）→ 10.3s 并行
- IMAP 快通道 3.0s 到件 OTP，住宅 IP（76.34.34.64 Louisville KY Charter）

## 失败案例 1：主号已注册（JenniferMitchell9500）

- create 400 "An account already exists for this email address"
- 根因：**主号已在 OpenAI 注册**（accounts.jsonl 有 7 条痕迹含 3 条 ok），
  但号池 state `used` 未标记 → 被误当可用
- 教训：**号池 used 标记与 accounts.jsonl 注册记录脱节**——注册成功过的主号
  不被自动标 used，重复拿它注册必撞已存在

## 失败案例 2：收码通道失效 + NameError（KaitlynMendez1926）

- IMAP 换 access_token 400 + Graph 也拿不到 → OTP 超时 334s 白等
- **NameError bug**：`ms_graph.py` 抛 `MailClientError` 但未 import → 真实错误被掩盖
  成 NameError。已修复（补 import）。
- 反馈问题：主号 token 失效时只打印 `[IMAP] token 刷新 status=400: {}`，
  无账号级归因（是 refresh_token 过期还是 MS 拒绝？）

## 干净主号 IMAP 可用性（accounts.jsonl 无痕迹）

- 26 个干净可用主号中 10 个抽样：6/10 IMAP 可用（EricPatel3950/DavidHodges3764/
  ColtonKlein3378/StacyBerry5837/JoseWhitney3017/KirstenScott5455）
- 4/10 token 刷新 400（KaitlynMendez1926/KellyOrtiz1695/NicholasMiller9570 等），
  2/10 被 MS 拒 XOAUTH2（JasonCopeland6778）→ 降级 Graph
- 结论：**大量干净主号的 refresh_token 已失效**（占 40%），影响批量生产

## 反馈缺口（新增）

1. **号池 used 与注册记录不联动**：成功注册后主号不标 used，重复用 → create 400 已存在
2. **主号 token 失效无账号级归因**：`[IMAP] token 刷新 status=400` 应标注
   refresh_token 过期/scope 缺失/MS 拒绝，并支持批量预检淘汰失效主号
3. Graph 收码超时提示已存在（150s+），但 token 失效的预检缺失——批量跑前应先验
   IMAP/Graph 可用性，跳过失效主号
