# 试用资格研究（2026-08-13）

> 纯研究：梳理 ChatGPT Plus 免费试用（promo）的完整机制、判定逻辑、社区方案，以及注册机号无资格的根因。

## 核心结论

**试用资格 = OpenAI 灰度测试（grayscale test）发放的 promo campaign，针对特定用户组/地区，无法制造、只能批量注册后筛选。** 注册机号无资格的根因（08-14 最终验证）：**注册全流程的浏览器真实性**——纯协议只在 create 阶段补 so，前段 signin/register/about-you 是纯 HTTP 无浏览器交互，结构性出不了资格。

---

## 一、试用资格是什么

- **promo 形式**：`promo_campaign_id` 两种
  - `plus-1-month-free` —— Plus 个人版，首月 $0
  - `team-1-month-free` —— Business（5 seats），首月 $0
- **判定字段**：`accounts/check` 的 `eligible_promo_campaigns.plus`（有 `plus` 键 = 在灰度名单）
- **本质**：不是账号固定属性，是 OpenAI 按活动/灰度动态发放的营销资格

## 二、判定机制（两层）

1. **静态发现层**：`accounts/check` 返回 `eligible_promo_campaigns`，其中 `plus` 键的 `id` 是真实的 promo id。字段缺失 = 不在灰度名单候选。
2. **服务端判定层**：`POST /backend-api/payments/checkout` 时，OpenAI 服务端根据**账号画像 + 出口地区 + 风控**决定 promo 是否被接受。静态字段有 `plus` ≠ checkout 一定成功（反之亦然，见 08-12 实证：手工 iCloud 号静态 null 但 checkout promo 生效）。

**结论**：静态字段只是"发现"，真正的资格在 checkout 时服务端判定。

## 三、社区方案（openai-promo-bypass 源码拆解）

完整流程：
```
session JSON(accessToken) → JP 代理 → accounts/check 查 eligible_promo_campaigns.plus.id
→ 若有则用真实 promo id，否则用硬编码 plus-1-month-free
→ POST payments/checkout → 拿 Stripe checkout URL
```

checkout 请求关键参数：
```json
{
  "plan_name": "chatgptplusplan",
  "entry_point": "all_plans_pricing_modal",
  "checkout_ui_mode": "hosted",
  "billing_details": {"country": "ID", "currency": "IDR"},
  "promo_campaign": {"promo_campaign_id": "plus-1-month-free", "is_coupon_from_query_param": false}
}
```

**关键差异（vs 我们）**：
- 社区用 `tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)` —— **随机 TLS 扩展顺序**，避免 TLS 指纹雷同
- 我们用 `curl_cffi.Session(impersonate="chrome142")` —— **固定 TLS 指纹**，所有账号 JA3/JA4 完全相同

## 四、pthdnu 字段（已证伪"TLS 指纹雷同"假设）

实测 8 个 Outlook 存活号的 `accounts/check.account.pthdnu` **完全相同**（`9a077e7760...`），但进一步验证推翻了初始假设：

| 维度 | pthdnu 是否变化 |
|---|---|
| 不同账号（8 个） | ❌ 相同 |
| 不同 TLS 指纹（3 个 impersonate：chrome120/131/142） | ❌ 相同 |
| 不同出口 IP（US/JP/NL） | ❌ 相同 |
| ≠ sha256(UA) | ❌ |

**结论：pthdnu 是全局固定值（疑为 plan_type/free 相关的固定 hash），不是批量识别信号，也不是 TLS 指纹/IP 的 hash。** "pthdnu 雷同 = 被风控聚类"的假设不成立。

TLS 指纹差异化（impersonate 轮换）已实现但**对 pthdnu 无效**；仍可作为通用反指纹能力保留（`browser.impersonate_rotate`，默认关）。

## 五、字段实测（现有注册机号，2026-08-13）

| 字段 | 值 | 含义 |
|---|---|---|
| `eligible_promo_campaigns` | 0/20 缺失 | 不在灰度名单 |
| `user_segmentation_info` | `None` | 无灰度分群 |
| `pthdnu` | 8/8 完全相同 | TLS 指纹雷同 |
| `created_time` | 08-11 | 新号（2 天） |
| `processor.*.has_customer_object` | `false` | 未绑卡 |
| `plan_type` | `free` | 免费 plan |
| `eligible_for_reactivation` | `true` | 可重新激活 |

## 六、根因推断（注册机号无资格）

1. **新号**——灰度测试倾斜老号（survey 记录"老 Free 号登录有几率刷 1 个月 Plus $0"）；我们的号仅 2 天
2. **无灰度分群**——`user_segmentation_info=None`，账号画像没进任何分群桶
3. ~~TLS 指纹雷同~~——已证伪（pthdnu 是全局固定值，不随 TLS/IP/账号变，见第四节）

## 七、可操作建议

1. **等号变老**：活 1-2 周后再查资格，老号进灰度概率更高（与存活追踪天然协同）。
2. **批量筛选**：无法制造，只能注册后筛。静态字段不可靠（08-12 已证），checkout 探测才准（但会创建 checkout 草稿）。
3. **（保留能力）TLS 指纹差异化**：对资格/pthdnu 无效，但打破 JA3 雷同仍是通用反指纹好实践；代码已实现，`browser.impersonate_rotate` 控制，默认关。

## 八、最终验证：资格 = 邮箱域，纯协议 iCloud 号有资格（08-14 修正）

**关键教训**：之前用 `eligible_promo_campaigns` 静态字段判"0 资格"是判据错误——静态字段对所有新号都是 null，真正的资格要看 **checkout 时 promo 是否被接受**（`scan_trial_eligibility.py --probe`，JP 出口）。

**实测（checkout 探测）**：

| 账号 | 邮箱域 | 注册方式 | checkout 真实资格 |
|---|---|---|---|
| textual-henna-3x | iCloud | vm so + 随机行为 | ✅ promo 接受 |
| retests.alchemy_1g | iCloud | browser so | ✅ promo 接受 |
| result_starts.5w | iCloud | vm so 原版 | ✅ promo 接受 |
| RuhlandAuber48 | Outlook | vm so | ❌ promo 拒绝 |
| reg_87fa4c | cloudmail | vm so | ⚠️ 账号已死(1.3h) |

**结论**：资格 = **邮箱域**（iCloud 有资格，Outlook 无资格），不是注册方式。纯协议 iCloud 号 **3/3 checkout 有资格**。

- survey 08-12 的"手工 iCloud vs 注册机 Outlook"对照混淆了变量——真正的区别是邮箱域
- 纯协议完全能高效产资格号：**iCloud 号源 + 纯协议注册 + JP 出口 checkout**

**高效出资格的正确路径**：
1. 用 iCloud 号源纯协议注册（高效，~37s/号）
2. JP 出口 checkout 探测（scan_trial_eligibility --probe）确认 promo 接受
3. 有资格的号走 checkout 流程（openai-promo-bypass 方式）拿试用

## 参考来源

- [datfooldive/openai-promo-bypass](https://github.com/datfooldive/openai-promo-bypass) —— 源码拆解
- [shi-YangYang/plus-extractor](https://github.com/shi-YangYang/plus-extractor)
- [Abel-j/ABCard](https://github.com/Abel-j/ABCard)
- [ChatGPT Plus 低价订阅技术拆解（80aj）](https://www.80aj.com/2026/05/08/chatgpt-plus-discount-bypass/)
- survey-open-source-202608.md 第 718-750 行（08-11/08-12 资格研究）
