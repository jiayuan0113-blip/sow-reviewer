# 抽取字段规格（Field Spec）

抽取 agent 从每份 SoW（source of wealth，财富来源）材料里抽出下面的字段，输出一个 JSON（schema 在 `sow_reviewer/schemas.py`，两边必须一致）。

原则：**模型只抽证据，不下结论。** 每个抽到的字段都要在 `evidence` 里附原文片段；文件里没有的字段填 `null`，不许猜；看不清或自相矛盾的写进 `missing_or_unclear`。「算不算已核实」由 `verify()` 按规则表判定（见 `docs/verify_rules.md`），不由模型判。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `document_type` | enum | 是 | `bank_statement` / `share_sale_agreement` / `trust_deed` / `salary_letter` / `gift_letter` / `other` |
| `subject_name` | string | 是 | 这份材料证明的是谁的财富来源（客户本人） |
| `issuer` | string \| null | 否 | 出具材料的机构或个人（银行、公司 HR、律所、赠与人） |
| `counterparty` | string \| null | 否 | 资金的另一方（买方、雇主、受托人、赠与人） |
| `document_date` | date (YYYY-MM-DD) \| null | 否 | 材料的签署或出具日期 |
| `period_start` | date \| null | 否 | 覆盖期间开始（流水、薪资期间） |
| `period_end` | date \| null | 否 | 覆盖期间结束 |
| `currency` | string (ISO 4217) \| null | 否 | 主金额的币种 |
| `principal_amount` | number \| null | 否 | 这份材料的主金额：成交价、分配额、年薪、赠与额、期末余额 |
| `sow_category` | enum | 是 | `employment_income` / `business_sale` / `inheritance_or_trust` / `gift` / `investment_proceeds` / `property_sale` / `unknown` |
| `transactions` | Transaction[] | 否 | 流水里的关键进出账；非流水材料可以为空 |
| `evidence` | Evidence[] | 是 | 每个非空字段至少一条 `{field, quote}`，quote 必须是原文逐字片段 |
| `missing_or_unclear` | string[] | 是 | 没抽到的必填项、相互矛盾的地方、看不清的内容，没有则空数组 |

## Transaction

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | date \| null | 入账日期 |
| `description` | string | 流水描述原文 |
| `direction` | enum | `credit` / `debit` |
| `amount` | number | 绝对值 |
| `currency` | string | 币种 |
| `counterparty` | string \| null | 描述里能看出的对手方 |

只抽**单笔 ≥ 10,000 的**进出账，或者描述里带「salary / consideration / distribution / gift / sale」这类字眼的。小额日常消费不抽。

## Evidence

| 字段 | 类型 | 说明 |
|---|---|---|
| `field` | string | 对应的顶层字段名，交易用 `transactions[i].amount` 这种写法 |
| `quote` | string | 原文逐字片段，20–200 字符 |

## 数值与日期规则

- 金额去掉千分位和货币符号，`S$2,750,000.00` → `2750000`，币种单独填 `SGD`。
- 日期统一 `YYYY-MM-DD`；只有年月的填该月 1 号并在 `missing_or_unclear` 里注明。
- 材料里同时出现两种币种，`principal_amount` 取合同主币种，另一币种的金额放 `missing_or_unclear` 说明。
- 中英混排的材料，字段值保留原文语言，不翻译。

## 5 个合成案例覆盖什么

| 案例 | 文件 | 类型 | 故意埋的点 |
|---|---|---|---|
| SOW-001 | `cases/sow-001-bank-statement.md` | 流水 | 一笔 60,000 现金存入没有说明来源 |
| SOW-002 | `cases/sow-002-share-sale-agreement.md` | 股权出售协议 | 分两期付款，只有第一期有付款日期 |
| SOW-003 | `cases/sow-003-trust-deed.md` | 信托契约 + 分配函 | 信托本金是 USD，分配是 SGD |
| SOW-004 | `cases/sow-004-salary-letter.md` | 薪资证明 | 底薪和奖金分开写，年薪要自己算 |
| SOW-005 | `cases/sow-005-gift-letter.md` | 赠与函 | 赠与人资金来源只有一句话，没有佐证 |

全部为合成数据，人名、公司名、银行名、账号均为虚构。
