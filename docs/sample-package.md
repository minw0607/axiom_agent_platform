# A sample package, end to end

This page follows one synthetic borrower package through the platform: the documents a relationship manager submits, the figures the platform extracts, the ratios its tools compute, the memo a model drafts, and how every claim in that memo is checked.

The package is **pkg-04**, a fictional specialty printing business asking for a $1,400,000 term loan. Its scenario is *weak DSCR*: the business is profitable, but the new loan's payments would exceed its cash flow. The documents are copied from [`data/synthetic/packages/pkg-04/`](../data/synthetic/packages/pkg-04/). The memo is a real model output, recorded during a live run, which the keyless demo replays ([`data/recorded/drafter/v3/pkg-04.json`](../data/recorded/drafter/v3/pkg-04.json)).

All data is synthetic. The business does not exist.

## 1. What the relationship manager submits

A package holds five short documents. Line numbers are shown on the left because citations point to them: "income statement, line 11" means the EBITDA row below.

**Income statement** (`pkg-04-income_statement`)

```text
 1  # Income statement
 2
 3  Synthetic Borrower 04 (specialty printing)
 4  Fiscal years ending December 31. Amounts in US dollars; parentheses are negative.
 5
 6  | Line item | FY2023 | FY2024 | FY2025 |
 7  |---|---|---|---|
 8  | Revenue | 9,100,000 | 9,191,000 | 8,915,270 |
 9  | Cost of goods sold | 5,824,000 | 5,882,240 | 5,705,773 |
10  | Operating expenses | 2,229,500 | 2,251,795 | 2,184,241 |
11  | EBITDA | 1,046,500 | 1,056,965 | 1,025,256 |
12  | Depreciation and amortization | 360,000 | 360,000 | 360,000 |
13  | Interest expense | 208,000 | 156,000 | 104,000 |
14  | Income tax expense | 119,625 | 135,241 | 140,314 |
15  | Net income | 358,875 | 405,724 | 420,942 |
```

**Balance sheet** (`pkg-04-balance_sheet`)

```text
 1  # Balance sheet
 2
 3  Synthetic Borrower 04 (specialty printing)
 4  Fiscal years ending December 31. Amounts in US dollars; parentheses are negative.
 5
 6  | Line item | FY2023 | FY2024 | FY2025 |
 7  |---|---|---|---|
 8  | Cash and equivalents | 455,000 | 459,550 | 445,764 |
 9  | Accounts receivable | 1,092,000 | 1,102,920 | 1,069,832 |
10  | Inventory | 728,000 | 735,280 | 713,222 |
11  | Total current assets | 2,457,000 | 2,481,570 | 2,407,123 |
12  | Total assets | 5,357,000 | 5,381,570 | 5,307,123 |
13  | Total current liabilities | 1,638,000 | 1,654,380 | 1,604,749 |
14  | Total debt (bank and term loans) | 2,600,000 | 1,950,000 | 1,300,000 |
15  | Total liabilities | 4,238,000 | 3,604,380 | 2,904,749 |
16  | Total equity | 1,119,000 | 1,777,190 | 2,402,374 |
```

**Cash flow statement, selected lines** (`pkg-04-cash_flow`)

```text
 1  # Cash flow statement (selected lines)
 2
 3  Synthetic Borrower 04 (specialty printing)
 4  Fiscal years ending December 31. Amounts in US dollars; parentheses are negative.
 5
 6  | Line item | FY2023 | FY2024 | FY2025 |
 7  |---|---|---|---|
 8  | Capital expenditures | 396,000 | 396,000 | 396,000 |
 9  | Repayment of long-term debt | 650,000 | 650,000 | 650,000 |
```

**Loan application** (`pkg-04-loan_application`)

```text
 1  # Loan application
 2
 3  Applicant: Synthetic Borrower 04 (specialty printing)
 4  Product: commercial term loan
 5  Requested amount: $1,400,000
 6  Interest rate: 8.25% fixed
 7  Term: 5 years
 8  Amortization: 5 years, level monthly payments
 9  Purpose: equipment purchase and working capital
```

**Business description** (`pkg-04-business_description`)

```text
 1  # Business description
 2
 3  Synthetic Borrower 04 (specialty printing) is a fictional specialty printing business founded in 2004, with about 60 employees.
 4
 5  The company serves commercial customers in its region. No single customer accounted for more than 14% of FY2025 revenue.
 6
 7  Management has operated the business for more than five years. The owners guarantee existing bank debt personally.
 8
 9  This document is synthetic and describes no real business.
```

Before any model reads these, the platform masks personal identifiers (there are none here) and screens for instructions planted in the text ([guardrails, ADR 0004](adr/0004-guardrail-placement.md)).

## 2. What the platform extracts

A model reads each document and returns every figure with the line it came from. Code then checks each one against that line and rejects any figure the line does not contain ([ADR 0007](adr/0007-deterministic-computation.md)). Three of the extracted figures:

| Figure | Year | Value | Source |
|---|---|---|---|
| EBITDA | FY2025 | 1,025,256 | income statement, line 11 |
| Interest expense | FY2025 | 104,000 | income statement, line 13 |
| Repayment of long-term debt | FY2025 | 650,000 | cash flow, line 9 |

Reconciliation checks then confirm that the statements agree with each other. For FY2025, total liabilities (2,904,749) plus equity (2,402,374) equal total assets (5,307,123), so the check passes.

## 3. What the tools compute

Ratios come from tested Python, never from a model. The deciding one for pkg-04 is the **pro forma debt service coverage ratio**: can this year's cash flow cover the existing debt service plus the new loan's payments?

| Step | Calculation | Result |
|---|---|---|
| New loan's annual payments | $1,400,000 at 8.25% over 60 level monthly payments: 28,554.75 × 12 | 342,657.00 |
| Debt service with the new loan | interest 104,000 + principal 650,000 + new payments 342,657.00 | 1,096,657.00 |
| Pro forma DSCR | EBITDA 1,025,256 ÷ 1,096,657.00 | **0.93** |

The credit policy sets a minimum of 1.25 (CP-4.1) and a floor of 1.00 below which a request cannot be approved even as an exception (CP-6.3). At 0.93, both rules fire, and the platform raises two high-severity risk flags: `DSCR_BELOW_MIN` and `DSCR_BELOW_DECLINE_FLOOR`.

## 4. The memo a model drafted

This is the live draft, with the citation behind each claim shown in brackets. Its figures are written without thousands separators, as the model chose to write them.

**Borrower and request**
- Synthetic Borrower 04 (specialty printing) is a fictional specialty printing business founded in 2004, with about 60 employees. *[business description, retrieved passage]*
- The borrower requested 1400000 at an interest rate of 8.25, with a term of 5 years and amortization of 5 years. *[loan application, lines 5, 6, 7, 8]*

**Financial analysis**
- FY2025 revenue was 8915270, EBITDA was 1025256, interest expense was 104000, and net income was 420942. *[income statement, lines 8, 11, 13, 15]*
- FY2025 total current assets were 2407123, total current liabilities were 1604749, total assets were 5307123, total liabilities were 2904749, total debt was 1300000, and total equity was 2402374. *[balance sheet, lines 11, 13, 12, 15, 14, 16]*
- Computed FY2025 ratios include ebitda_margin of 0.11, current_ratio of 1.50, leverage of 1.27, interest_coverage of 6.40, debt_to_equity of 0.54, dscr of 1.36, revenue_growth of -0.03, proposed_annual_debt_service of 342657.00, pro_forma_dscr of 0.93, and pro_forma_leverage of 2.63. *[income statement and balance sheet lines, loan application lines 5–8]*

**Risk factors**
- Risk flag DSCR_BELOW_MIN is high severity and states that pro forma DSCR 0.93 is below the policy minimum of 1.25 under CP-4.1. *[income statement, line 11; loan application, lines 5–8]*
- Risk flag DSCR_BELOW_DECLINE_FLOOR is high severity and states that pro forma DSCR 0.93 is below 1.00 under CP-6.3. *[income statement, line 11; loan application, lines 5–8]*
- No failed reconciliation check was reported in the input package. *[income statement, line 8; balance sheet, line 12]*

**Draft recommendation:** decline
- Draft recommendation for credit officer consideration is decline because DSCR_BELOW_DECLINE_FLOOR was raised and the request includes 1400000 at 8.25 over a term of 5 years with amortization of 5 years. *[loan application, lines 5–8]*

The recommendation is a draft. The memo then waits for a credit officer, who is not the person who requested it, to approve, reject or ask for changes.

## 5. How each claim is checked

The memo is not free text. Each statement is a structured claim with its citations, so code can check them one by one. The revenue claim above is stored like this:

```json
{
  "text": "FY2025 revenue was 8915270, EBITDA was 1025256, interest expense was 104000, and net income was 420942.",
  "citations": [
    {"doc_id": "pkg-04-income_statement", "locator": "line 8"},
    {"doc_id": "pkg-04-income_statement", "locator": "line 11"},
    {"doc_id": "pkg-04-income_statement", "locator": "line 13"},
    {"doc_id": "pkg-04-income_statement", "locator": "line 15"}
  ]
}
```

Five checks run on every claim. The first four are code, so they give the same answer every time; the fifth is a model.

| Check | What it asks | For the revenue claim |
|---|---|---|
| **Citation coverage** | Does every factual claim outside the recommendation have a citation? | Yes, four |
| **Citations point somewhere real** | Does each citation name a document in the package, and a line that exists and is not blank, or a passage that was actually retrieved? | Yes: lines 8, 11, 13 and 15 exist |
| **Number provenance** | Does every number in the claim match an extracted figure, a tool output, a policy threshold, or a number in the documents, allowing only the rounding its precision shows? | Yes: 8915270, 1025256, 104000 and 420942 are all extracted figures |
| **Citation support (code)** | Does a claim that states an extracted figure cite that figure's own line? | Yes: revenue cites line 8, EBITDA line 11, interest line 13, net income line 15 |
| **Citation support (grader)** | Do the cited lines, plus the platform's tool outputs for ratios and flags, support every part of the wording? | An AI grader, a different model from the drafter, answers supported or not supported, with a reason |

Two examples of what the checks catch (the tests cover both, on other packages):

- The same revenue claim **citing line 11 (EBITDA) instead of line 8** fails the code check: revenue's own line is not cited.
- A claim that **"FY2025 revenue was 8995270"** when the income statement says 8,915,270 fails number provenance: the number is in no source.

A failed check sends the draft back to the drafter once, with the problem named. If the corrected draft still fails, the job stops rather than reaching a credit officer ([ADR 0004](adr/0004-guardrail-placement.md)). Separate checks on the same draft look for credit decisions stated as made, investment advice, text that is not memo content, and personal data.

The grader's role is limited until it is calibrated against a person's judgement: its scores are reported, but do not yet block a release ([ADR 0008](adr/0008-evaluation-thresholds.md)).

## 6. How the memo scores against the golden answers

Every package has expected answers, written from the credit policy ([review page](../evals/golden/v1/answers.md)). For pkg-04:

| Golden answer | The memo |
|---|---|
| Expected risk flags: `DSCR_BELOW_MIN`, `DSCR_BELOW_DECLINE_FLOOR` | Both raised |
| Acceptable draft recommendation: decline only | Decline |
| Key finding: pro forma DSCR of 0.93 is below the 1.25 minimum (CP-4.1) | Stated |
| Key finding: 0.93 is below the 1.00 decline floor (CP-6.3), so no exception is possible | Stated |

The evaluation runner scores all 25 golden packages this way ([live results](../evals/results/golden-live.md)).

## The other packages

The catalog has 25 packages, each built to test one scenario: healthy borrowers, a leverage breach, a balance sheet that does not balance, a missing year, customer concentration, values exactly on a policy limit, a missing cash flow statement, and more. They are listed with what each is built to test in [`data/synthetic/README.md`](../data/synthetic/README.md).
