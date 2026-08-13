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

## 八、最终验证：纯协议结构性出不了资格（08-14）

**验证矩阵**（本轮全部实测，均为新注册 iCloud/Outlook 号）：

| 方案 | 资格 |
|---|---|
| vm so 原版（固定 15 坐标行为） | 0 |
| vm so + 改进行为（随机轨迹 28-48 次，自然节奏） | 0 |
| browser so（create 阶段真实 Chrome 采 so） | 0 |
| 浏览器手动注册（用户观察） | 大概率有 |

**结论**：资格 = **注册全流程**的浏览器真实性，不是单点（so/行为字段/TLS/邮箱域/年龄）能决定的。

- browser so 注册 ≠ 浏览器手动注册：前者只有 create 阶段用 Chrome 采 so，前段 signin/register/about-you 全是纯 HTTP；后者全程真实页面交互
- 所以"so 来源真实性"也不是关键（browser so 也 0）——真正的信号是**前段的浏览器交互**
- 纯协议（vm so 或 browser so）结构性缺前段行为 → 资格 0

**对"高效出资格"的最终含义**：
1. 纯协议方向**结构性死路**（so/行为/TLS/邮箱域全试过，出不了资格）
2. 唯一路径 = **浏览器全流程自动化注册**（Playwright 真实页面交互），牺牲效率换资格
3. 真正的"高效"课题 = 浏览器自动化的效率上限（并发浏览器池、精简流程、session 复用）

## 参考来源

- [datfooldive/openai-promo-bypass](https://github.com/datfooldive/openai-promo-bypass) —— 源码拆解
- [shi-YangYang/plus-extractor](https://github.com/shi-YangYang/plus-extractor)
- [Abel-j/ABCard](https://github.com/Abel-j/ABCard)
- [ChatGPT Plus 低价订阅技术拆解（80aj）](https://www.80aj.com/2026/05/08/chatgpt-plus-discount-bypass/)
- survey-open-source-202608.md 第 718-750 行（08-11/08-12 资格研究）
