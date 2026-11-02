# 抽取字段规格（Field Spec）

抽取 agent 从每份 SoW（source of wealth，财富来源）材料里抽出下面的字段，输出一个 JSON（schema 在 `sow_reviewer/schemas.py`，两边必须一致）。

原则：**模型只抽证据，不下结论。** 每个抽到的字段都要在 `evidence` 里附原文片段；文件里没有的字段填 `null`，不许猜；看不清或自相矛盾的写进 `missing_or_unclear`。「算不算已核实」由 `verify()` 按规则表判定（见 `docs/verify_rules.md`），不由模型判。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `document_type` | enum | 是 | `bank_statement` / `share_sale_agreement` / `trust_deed` / `salary_letter` / `gift_letter` / `other` |
| `subject_name` | string | 是 | 这份材料证明的是谁的财富来源（客户本人）。只写姓名，不带 Mr / Ms / Dr 这类称谓 |
| `issuer` | string \| null | 否 | 出具材料的机构或个人（银行、公司 HR、律所、赠与人） |
| `counterparty` | string \| null | 否 | 资金的另一方（买方、雇主、受托人、赠与人） |
| `document_date` | date (YYYY-MM-DD) \| null | 否 | 材料的签署或出具日期 |
| `period_start` | date \| null | 否 | 覆盖期间开始（流水、薪资期间） |
| `period_end` | date \| null | 否 | 覆盖期间结束 |
| `currency` | string (ISO 4217) \| null | 否 | 主金额的币种 |
| `principal_amount` | number \| null | 否 | 这份材料的主金额，按下面「主金额取哪个数」取 |
| `sow_category` | enum | 是 | `employment_income` / `business_sale` / `inheritance_or_trust` / `gift` / `investment_proceeds` / `property_sale` / `unknown`。流水的入账只有一种来源才填对应类别，混有多种来源或看不出来源填 `unknown` |
| `transactions` | Transaction[] | 否 | 流水里的关键进出账；非流水材料可以为空 |
| `amount_components` | AmountComponent[] | 否 | 只用于薪资证明没写年度总额的情况，其他情况留空 |
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

## AmountComponent

| 字段 | 类型 | 说明 |
|---|---|---|
| `label` | string | 分项名称，保留原文 |
| `amount` | number | 原文写的单次金额，不要自己折算成年 |
| `frequency` | enum | `monthly`（按月发）/ `annual`（一年一次，包括酌情发放的年度奖金） |
| `quote` | string | 这一分项的原文逐字片段 |

## Evidence

| 字段 | 类型 | 说明 |
|---|---|---|
| `field` | string | 对应的顶层字段名，交易用 `transactions[i].amount` 这种写法 |
| `quote` | string | 原文逐字片段，20–200 字符。校验时忽略 markdown 标记、表格竖线、大小写和空白差异，其他内容必须一字不差 |

## 主金额取哪个数

| document_type | `principal_amount` |
|---|---|
| `bank_statement` | 期末余额 |
| `share_sale_agreement` | 合同成交总价，分期付款也取总价。任何一期没有确定的付款日期或金额取决于未来条件，写进 `missing_or_unclear` |
| `trust_deed` | 这次分配给客户的金额，不取信托设立本金或信托净值 |
| `salary_letter` | 材料写明的年度薪酬总额。没写总额、只列分项的，这里填 `null`，把每个分项写进 `amount_components`，总额由代码计算，不要自己算。这种情况是正常的，不写进 `missing_or_unclear` |
| `gift_letter` | 赠与额 |

## 数值与日期规则

- 字段的值被涂黑或用占位符代替（如 `[NAME REDACTED]`）时视为材料里没有：字段填 `null`，并写进 `missing_or_unclear`。证件号、门牌号这类非抽取字段的部分打码是常规做法，不要写进 `missing_or_unclear`。
- 金额去掉千分位和货币符号，`S$2,750,000.00` → `2750000`，币种单独填 `SGD`。
- 日期统一 `YYYY-MM-DD`；只有年月的填该月 1 号并在 `missing_or_unclear` 里注明。
- `currency` 跟随 `principal_amount` 取的那个金额。材料里同时出现其他币种时，把那些金额写进 `missing_or_unclear`。
- 中英混排的材料，字段值保留原文语言，不翻译。
