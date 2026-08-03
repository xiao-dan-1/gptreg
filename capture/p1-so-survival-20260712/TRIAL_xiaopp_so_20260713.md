# Trial: pure protocol + xiaopp HAR so (n=1)

- **when**: 2026-07-13 ~17:16–17:18 (local)
- **cmd**: `python main.py -n 1 -v`
- **goal**: 测小PP HAR so 能否抬 create 通过率（纯协议，无浏览器）

## Config

| key | value |
|-----|--------|
| sentinel_source | pow |
| pow_so_source | xiaopp |
| create_browser_fallback | false |
| create_retries | 3 |
| proxy | 辣椒 US via chain |

## Pool before

- total=39 unused=10 used=11 bad=0 retrying=18

## Run

| field | value |
|-------|--------|
| main | AdamChan1252@outlook.com |
| reg | AdamChan1252+5fa46f@outlook.com |
| alias | true |
| device_id | 442dc14c-a72e-4efa-97a4-e7a7c31eee20 |
| region | US |
| name / birthdate | Brandon Harris / 1998-05-23 |

### Steps

| step | result |
|------|--------|
| providers / csrf / signin | OK |
| authorize 落点 | email-verification |
| OTP sentinel | pow, has_so=False（OTP 不带 HAR so，预期） |
| OTP | 264642 ms_oauth → validate page=about_you |
| warm about-you | 200 |
| create chatReq | http=200 so_required=True difficulty=06b6a5 |
| create headers | **mode=pow has_so=True so_len=2790 pow_so_source=xiaopp** |
| create #1 | 400 registration_disallowed |
| create #2 | 400 registration_disallowed |
| create #3 | 400 registration_disallowed |
| browser fallback | 未触发（默认关） |

### Endpoint error (raw)

```
POST https://auth.openai.com/api/accounts/create_account
HTTP 400
code: registration_disallowed
message: "Sorry, we cannot create your account with the given information."
         / "Sorry, we aren't able to create your account"
```

## Outcome

| metric | value |
|--------|--------|
| bucket | create_disallow |
| 表观 | 0/1 |
| 到 create | 1，通过率 0% |
| 基建噪声 | 0 |
| 小PP so 是否发出 | **是**（so_len=2790） |
| 小PP so 是否解 disallow | **否（本样本）** |

## Read

1. 纯协议 + xiaopp HAR so **链路正确**（create 已带头）。
2. 同 body×3 仍 `registration_disallowed` → 本根拒建，**不能**用 n=1 证伪/证实 so 银弹。
3. 与历史对照：pow 无 so 也曾 200（Eric/Embree）；browser 真 so 有过 200（Zachary）也有同根仍 400（Kaitlyn）。
4. 下一步：再测新根 n≥3 看比例；或 `pow_so_source: none` 做对照；优先补号后继续，勿同根连刷。

## Pool after (approx)

- Adam → retrying；unused ≈ 9（+原 10 新号里若 Adam 原算 unused 则 -1）
