# ADR 0007: Deterministic vs. LLM computation

**Status:** Accepted (week 3)

## Context

A credit memo stands on its numbers: debt service coverage, leverage, liquidity, interest coverage and trends. A wrong ratio can change a recommendation. Language models make arithmetic and transcription errors that read as fluent and confident, and they can produce a different answer to the same question on a different run. The release gate requires ratios to be 100% exact.

The platform still needs a model to read the documents and to write prose.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| The model computes ratios | Fewest moving parts; handles odd document layouts | Errors are silent and nondeterministic; exactness cannot be guaranteed or tested |
| Code tools compute ratios | Exact, repeatable, unit-testable; the formula is part of the result | Needs typed inputs, so extraction has to be reliable |
| Hybrid: the model computes, code verifies | Catches some errors | Two computations for every number; when they disagree, code wins anyway |

## Decision

**Code computes; the model reads and writes.**

- **Extraction.** The model copies figures from documents. It returns each value with the line it came from, and does no arithmetic.
- **Verification.** Code checks every extracted figure against its cited line, and against the column for its fiscal year in a table. A figure that does not match fails validation. The model gets one retry with the errors; a second failure fails the job (`output_validation_failed`).
- **Computation.** Ratios, pro forma debt service, policy flags and reconciliation checks are plain Python in `src/axiom/tools/`, using `Decimal`. Ratios round half-up to two places. The monthly payment is rounded to the cent before it is annualized, as a lender would bill it. A ratio with a zero or missing denominator is reported as undefined, never as zero, and an undefined ratio raises a flag rather than passing a threshold.
- **Writing.** The analyst and drafter receive computed values as strings, and their prompts say to use them exactly and never calculate. The week 5 number-provenance guardrail will check every number in the memo against these sources.
- **Recommendation consistency.** Code checks the drafted recommendation against the findings: a failed reconciliation requires "refer", and a failed policy standard rules out "approve".

## Consequences

- Ratio exactness is a unit test. The synthetic data generator has its own independent implementation of each formula, and the tool must match it exactly on every package.
- Each ratio result carries its formula and inputs, so a reviewer can recompute it by hand.
- Extraction is the main remaining source of numeric error, and it is checked line by line. Line-level checking depends on documents having line structure; scanned PDFs would need an OCR step that keeps positions.
- Pro forma DSCR assumes existing debt service continues alongside the new loan. The assumption is stated on the result and in the credit policy.
