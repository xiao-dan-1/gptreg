# 密码注册 + 2FA 激活 耗时记录（2026-08-07）

## 结论速览

- 主流程（verify_pwd_totp 密码注册 + enroll→activate_enrollment 2FA）**完全正常**
- `register 400 invalid_auth_step` = **代理出口 IP 被 OpenAI 风控**，非流程/OpenAI 变更
  （10808 通 → register 直接过；7890/cliproxy 被拒时 400）
- 本地 IMAP 快通道**有效**：IMAP 可用账号 OTP 15s vs 降级 Graph 167s，单账号 ~34s vs ~186s
- 号池 IMAP 账号级差异：~12/15 可用，~3/15 被 MS 账号级拒绝（authenticated but not connected，与 IP 无关）

## 单次注册耗时明细（成功 ×3，均 mfa_enabled=True 落盘）

| 阶段 | Brittany(10808) | Marilyn(7890) | Nathan(7890) |
|---|---|---|---|
| 代理出口 | 10808(通) | 7890 Azure US | 7890 Azure US |
| IMAP | 降级 Graph | 降级 Graph | **快通道** |
| send_otp(含 signin/authorize/register) | 6.5s | 7.1s | 6.6s |
| OTP 等待 | 157.3s | 167.5s | **15.1s** |
| create_account+session | 16.0s | 15.4s | 15.6s |
| enroll+activate+落盘 | ~3s | ~3s | ~3s |
| **总耗时** | **182.4s** | **186.3s** | **33.8s** |

账号：BrittanyWilliams7004 / MarilynOrtiz6396 / NathanRice4629（均 @outlook.com）

### 反馈修复后验证（2026-08-07 追加）

- BrittanyHunter7350@outlook.com，**动态链式**（7890隧道→cliproxy sid-2As2LXe5）出口 107.216.230.187（US Fair Oaks, AT&T 住宅IP）
- 总耗时 200.2s：send_otp 9.6s / OTP 168.1s(Graph降级) / create 192.1s / session 195.7s / enroll+activate 3.7s
- IMAP 被 MS 拒（authenticated but not connected）→ 降级 Graph（该账号非 IMAP 可用账号）
- 反馈修复验证：注册身份输出(Oliver Foster / 2000-09-21) ✓；`proxy_used` 落盘 ✓
- 关键结论：**动态链式=正确用法**（住宅IP register 通过）；手动 `--proxy` 固定 IP 连续注册会触发 OpenAI IP 风控（register 400 invalid_auth_step）

### Graph 优化验证（2026-08-07 追加，$filter + $top 递减）

| 账号 | IMAP | OTP 耗时 | 总耗时 |
|---|---|---|---|
| BrittanyHunter7350（优化前） | 被拒→Graph | 168.1s | 200.2s |
| CourtneyAnderson8650（$top5） | 被拒→Graph | 34.4s | 80.6s |
| PatrickOneill9904（$top2） | 不可用→降级 | **21.9s** | **56.3s** |

- PatrickOneill9904 明细：send_otp 12.7s / OTP 21.9s / create 47.5s / session 50.9s / enroll+activate 4.4s
- 趋势：Graph 降级收码 168s→34s→22s（$filter 减负 + 索引延迟波动，需多样本确认）
- 反馈缺口：verify_pwd_totp 未配置 logging，IMAP 降级/Graph 进度日志不可见（logger 不输出）

## 号池 IMAP 可用性（2026-08-07 check_imap --limit 15）

- 可用 12/15（JenniferMitchell9500, JohnOwens2952, BrandonNichols1400, LarryHoffman7534,
  TracyHenry4340, JenniferPhillips9261, ThomasRivers7260, JasmineMcconnell9909, TrenhHattie36,
  BeatheFulwood6282, EmbreeNicholas183, BengelsdorfSalato25）— 均 ~3s 连接
- 被 MS 拒 3/15（LeslieChavez6274, EricWilliams3405, QuentinKaboos152）= `authenticated but not connected`，降级 Graph
- 未用主号抽查：NathanRice4629/DavidPatton9433/KaylaDominguez7284/BenjaminSmith8845 可用；
  CharlesBaker5628 被拒

## 性能瓶颈与建议

- 单账号 34s（IMAP 快）vs 186s（Graph 降级）——瓶颈 100% 在 OTP 等待
- 批量生产应**优先选 IMAP 可用账号**（可先 check_imap 打标）
- 被 MS 拒账号自动降级 Graph 仍可注册（兜底 OK），只是慢 5.5x

## 反馈缺口（待改进）

1. IMAP 失败/降级 Graph 无显式提示 → 用户只见 `[OTP]` 静默 150s+
2. verify_pwd_totp 落盘缺 `proxy_used`（accounts.jsonl 该字段为空，不利于归因）
3. batch_totp 选号不区分 IMAP 可用性（撞上被拒账号即降级慢速）
