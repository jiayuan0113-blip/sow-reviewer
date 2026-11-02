"""Pydantic schemas for the extraction agent's structured output.

Must stay in sync with data/field_spec.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

DocumentType = Literal[
    "bank_statement",
    "share_sale_agreement",
    "trust_deed",
    "salary_letter",
    "gift_letter",
    "other",
]

SowCategory = Literal[
    "employment_income",
    "business_sale",
    "inheritance_or_trust",
    "gift",
    "investment_proceeds",
    "property_sale",
    "unknown",
]


class Transaction(BaseModel):
    date: str | None = Field(None, description="YYYY-MM-DD, or null if not stated")
    description: str = Field(description="Transaction description, verbatim")
    direction: Literal["credit", "debit"]
    amount: float = Field(description="Absolute value, no thousands separators")
    currency: str = Field(description="ISO 4217 code, e.g. SGD")
    counterparty: str | None = Field(None, description="Other party if visible in the description")


class AmountComponent(BaseModel):
    label: str = Field(description="Component name, verbatim")
    amount: float = Field(description="Amount per payment as written, not annualised")
    frequency: Literal["monthly", "annual"]
    quote: str = Field(description="Verbatim excerpt from the document for this component")


class Evidence(BaseModel):
    field: str = Field(description="Top-level field name, or transactions[i].amount for a transaction")
    quote: str = Field(description="Verbatim excerpt from the document, 20-200 characters")


class ExtractedDocument(BaseModel):
    document_type: DocumentType
    subject_name: str = Field(description="The client whose source of wealth this document evidences, name only, no title such as Mr or Ms")
    issuer: str | None = Field(None, description="Institution or person that issued the document")
    counterparty: str | None = Field(None, description="The other party to the funds: buyer, employer, trustee, donor")
    document_date: str | None = Field(None, description="YYYY-MM-DD")
    period_start: str | None = Field(None, description="YYYY-MM-DD")
    period_end: str | None = Field(None, description="YYYY-MM-DD")
    currency: str | None = Field(None, description="ISO 4217 code of principal_amount")
    principal_amount: float | None = Field(None, description="The document's main figure: price, distribution, annual pay, gift, closing balance")
    sow_category: SowCategory
    transactions: list[Transaction] = Field(default_factory=list)
    amount_components: list[AmountComponent] = Field(
        default_factory=list, description="Salary letters that list pay components but state no annual total"
    )
    evidence: list[Evidence] = Field(description="At least one entry per non-null field")
    missing_or_unclear: list[str] = Field(default_factory=list, description="Required fields not found, contradictions, illegible content")

    @model_validator(mode="after")
    def _total_salary_components(self) -> ExtractedDocument:
        """The model lists pay components and this code adds them up, so no total is model arithmetic."""
        if self.document_type != "salary_letter" or not self.amount_components:
            return self
        self.principal_amount = round(
            sum(c.amount * (12 if c.frequency == "monthly" else 1) for c in self.amount_components), 2
        )
        quoted = {e.quote for e in self.evidence if e.field == "principal_amount"}
        self.evidence += [
            Evidence(field="principal_amount", quote=c.quote) for c in self.amount_components if c.quote not in quoted
        ]
        return self
