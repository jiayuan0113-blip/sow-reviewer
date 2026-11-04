# verify() 规则表

`verify(extracted: ExtractedDocument) -> VerificationResult` 不调模型，只看抽取结果和这张表，给每个字段定一个档位，再汇总成整份材料的档位。

## 字段档位

| 档位 | 含义 |
|---|---|
| `verified` | 字段非空，且 evidence 里有对应的 quote |
| `unsupported` | 字段非空，但 evidence 里没有它的 quote（模型可能在猜） |
| `missing` | 字段为空 |
| `not_applicable` | 这种材料类型本来就不要求这个字段 |

## 每种材料的必填字段

| document_type | 必填 |
|---|---|
| bank_statement | subject_name, issuer, period_start, period_end, currency, principal_amount, transactions（至少 1 笔） |
| share_sale_agreement | subject_name, counterparty, document_date, currency, principal_amount |
| trust_deed | subject_name, issuer, document_date, currency, principal_amount |
| salary_letter | subject_name, issuer, document_date, currency, principal_amount |
| gift_letter | subject_name, counterparty, document_date, currency, principal_amount |

不在表里的字段一律 `not_applicable`。`document_type` 是 `other` 时没有规则可查，整份直接 `insufficient`。

## 整份材料的档位

按顺序判，命中第一条就停：

1. 任一必填字段 `missing` 或 `unsupported` → `insufficient`
2. `missing_or_unclear` 非空 → `needs_follow_up`
3. 流水里有单笔 ≥ 50,000 的 credit 且 counterparty 为空 → `needs_follow_up`（SOW-001 的现金存入就是这条）
4. sow_category 是 `gift` 且 principal_amount ≥ 100,000 → `needs_follow_up`（赠与要补赠与人资金来源，SOW-005）
5. 其余 → `verified`

## 返回结构

```python
@dataclass
class FieldCheck:
    field: str
    status: Literal["verified", "unsupported", "missing", "not_applicable"]

@dataclass
class VerificationResult:
    document_type: str
    overall: Literal["verified", "needs_follow_up", "insufficient"]
    fields: list[FieldCheck]
    reasons: list[str]   # 命中了哪条规则，人话
```

## 单元测试至少覆盖

- 5 个合成案例各一个（用 `data/expected/` 里的值手工拼 ExtractedDocument）
- 一个 evidence 缺失导致 `unsupported` 的例子
- 一个 `bank_statement` 没有 transactions 导致 `insufficient` 的例子
- 规则 3 和规则 4 的边界值（49,999 / 50,000；99,999 / 100,000）

---

# sanctions_lookup MCP 工具

`tools/sanctions_server.py`：用 `mcp.server.mcpserver.MCPServer`（mcp 2.x 里 FastMCP 改名叫 MCPServer，装饰器还是 `@server.tool()`），stdio 传输。

- 名单文件：`data/sanctions_synthetic.json`，自己造 10 个条目，字段 `name`、`aliases[]`、`list_name`、`dob`（可空）。**不要用任何真实名单**。
- 工具签名：`sanctions_lookup(name: str) -> dict`，返回 `{"query", "matches": [{"name", "list_name", "score"}], "checked_at"}`。
- 匹配：大小写无关，去标点，主名完全相同算 1.0，别名完全相同算 0.9，主名或别名的每个词都出现（顺序不限）算 0.7。
- 单元测试直接 import 函数本体测，不走 MCP。
- 接到 ADK：`McpToolset(connection_params=StdioConnectionParams(server_params=StdioServerParameters(command=sys.executable, args=["tools/sanctions_server.py"])))`，见 `sow_reviewer/agent.py`。
- 调用方是代码，不是模型（2026-11-04 起）：`run_screening` 从 `extracted` 里取 `subject_name` 和非空的 `counterparty`，用 `asyncio.gather` 同时查，每个名字一条结果 `{query, source, status: checked | unavailable, matches, checked_at}`。`compute_disposition` 要求每个该查的名字都是 `checked` 才给处置。
