# 案例与埋点（评测标签，不进 prompt）

这张表原来在 `data/field_spec.md` 末尾。`field_spec.md` 会被整份拼进抽取 agent 的 prompt，表留在那里等于把答案告诉模型，所以挪到这里。机器可读版本在 `index.json` 和 `data/expected/`。

全部为合成数据，人名、公司名、银行名、账号均为虚构。

| 案例 | 文件 | 类型 | 故意埋的点 | verify 档位 | 最终处置 |
|---|---|---|---|---|---|
| SOW-001 | `sow-001-bank-statement.md` | 流水 | 一笔 60,000 现金存入没有说明来源 | needs_follow_up | follow_up |
| SOW-002 | `sow-002-share-sale-agreement.md` | 股权出售协议 | 分两期付款，只有第一期有付款日期 | needs_follow_up | follow_up |
| SOW-003 | `sow-003-trust-deed.md` | 信托契约 + 分配函 | 信托本金是 USD，分配是 SGD | needs_follow_up | follow_up |
| SOW-004 | `sow-004-salary-letter.md` | 薪资证明 | 底薪和奖金分开写，年薪要自己算 | verified | evidenced |
| SOW-005 | `sow-005-gift-letter.md` | 赠与函 | 赠与人资金来源只有一句话，没有佐证 | needs_follow_up | follow_up |
| SOW-006 | `sow-006-bank-statement-clean.md` | 流水 | 最大数字是定存划转，主金额应取期末余额 | verified | evidenced |
| SOW-007 | `sow-007-salary-letter-undated.md` | 薪资证明 | 没有出具日期（必填缺失） | insufficient | insufficient |
| SOW-008 | `sow-008-gift-letter-threshold.md` | 赠与函 | 赠与额恰好 100,000，踩在阈值上 | needs_follow_up | follow_up |
| SOW-009 | `sow-009-share-sale-redacted-buyer.md` | 股权出售协议 | 买方名称被涂黑（必填缺失） | insufficient | insufficient |
| SOW-010 | `sow-010-salary-letter-sanctions-hit.md` | 薪资证明 | 文件干净，但客户命中合成名单的别名 | verified | escalate_edd |
