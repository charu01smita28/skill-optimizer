---
name: loan-calculator
description: Re-check a loan's stated figures — recompute monthly payment, total interest, and total amount paid from principal/rate/term, and flag every figure that disagrees with what the loan document states. Use for loan-origination verification before a contract is countersigned.
model: claude-haiku-4-5
primary_fields: ["valid"]
---

# Loan Calculator

Given a loan as JSON, recompute every monetary figure from `principal`, `annual_rate_pct`, `term_months`, and `origination_fee`, compare each against the figure printed on the loan document, and report any discrepancy. The output is what the loan-origination workflow uses to decide whether the document can be signed as-is or needs to go back to the lender.

## Input

A JSON object with:

- `loan_id` — string
- `lender` — string
- `currency` — ISO code, e.g. `"USD"` (informational; every amount is in this currency)
- `principal` — the amount borrowed, e.g. `25000.00`
- `annual_rate_pct` — APR as a percentage, e.g. `6.5` for 6.5% (`0` for an interest-free loan)
- `term_months` — integer, number of monthly payments
- `origination_fee` — flat amount added to the lifetime cost (`0` if none)
- `stated_monthly_payment`, `stated_total_interest`, `stated_total_paid` — the three figures printed on the loan document, to be checked

## Process

1. Read the loan JSON from the input file.
2. Implement `compute_loan_metrics(loan)` as a Python function following **The calculation** below; include the full function in your response.
3. Apply `compute_loan_metrics` to the input loan.
4. Save its return value as JSON to `output.json` in the current directory.

## The calculation

`compute_loan_metrics(loan)` computes the three figures in this order, rounding each monetary result to 2 decimal places before using it in the next step:

1. `monthly_rate` = `annual_rate_pct / 100 / 12` (no rounding — this is a rate, not an amount).
2. `monthly_payment`:
   - If `monthly_rate == 0`: `principal / term_months`.
   - Otherwise: `principal * monthly_rate * (1 + monthly_rate)^term_months / ((1 + monthly_rate)^term_months − 1)`.
3. `total_interest` = `monthly_payment * term_months − principal`.
4. `total_paid` = `principal + total_interest + origination_fee`.

It then builds `discrepancies` — a list with one entry, **in the fixed order `monthly_payment`, `total_interest`, `total_paid`**, for each figure whose computed value differs from the stated value:

```
{ "field":    "monthly_payment" | "total_interest" | "total_paid",
  "stated":   <the figure printed on the loan document>,
  "computed": <the recomputed figure>,
  "delta":    <computed - stated, rounded to 2 decimal places> }
```

A figure matches only when computed equals stated exactly after 2-decimal rounding — there is no tolerance band.

It returns:

```
{ "loan_id":       <loan_id>,
  "lender":        <lender>,
  "computed":      { "monthly_payment": ..., "total_interest": ..., "total_paid": ... },
  "discrepancies": [ ... ],
  "valid":         <true if discrepancies is empty, else false> }
```

## Output schema

```json
{
  "loan_id": "string (echo from input)",
  "lender": "string (echo from input)",
  "computed": {
    "monthly_payment": "number, 2 decimal places",
    "total_interest": "number, 2 decimal places",
    "total_paid": "number, 2 decimal places"
  },
  "discrepancies": [
    {
      "field": "monthly_payment | total_interest | total_paid",
      "stated": "number — the figure printed on the loan document",
      "computed": "number — the recomputed figure",
      "delta": "number — computed minus stated, 2 decimal places"
    }
  ],
  "valid": "boolean — true when discrepancies is empty"
}
```

## Notes

- `annual_rate_pct` is a percentage (`6.5` means 6.5%), not a fraction.
- The interest formula uses the standard amortizing-loan payment (PMT) — monthly compounding, fixed monthly payment over the full term.
- `origination_fee` is charged once at origination and is added to `total_paid`; it is not part of the monthly payment.
- Currency is informational only — do not convert; report amounts in the loan's own currency.
- Keep `discrepancies` in the fixed order monthly_payment → total_interest → total_paid so the report is stable across runs.
- `valid: true` with an empty `discrepancies` list means the loan document's arithmetic is internally consistent — not that the rate or fee is fair, only that the figures add up.
