---
name: invoice-validator
description: Re-check a vendor invoice's arithmetic — recompute subtotal, discount, tax, and total from the line items and rates, and flag every figure that disagrees with what the invoice states. Use for accounts-payable verification before an invoice is approved for payment.
model: claude-haiku-4-5
primary_fields: ["valid"]
---

# Invoice Validator

Given a vendor invoice as JSON, recompute every monetary figure from the line items and the stated rates, compare each against the figure printed on the invoice, and report any discrepancy. The output is what the accounts-payable workflow uses to decide whether the invoice can be approved as-is or needs to go back to the vendor.

## Input

A JSON object with:

- `invoice_id` — string
- `vendor` — string
- `currency` — ISO code, e.g. `"USD"` (informational; every amount is in this currency)
- `line_items` — list of `{ "description": string, "quantity": number, "unit_price": number }`
- `discount_pct` — percentage taken off the subtotal, e.g. `10` for 10% (`0` if none)
- `tax_rate` — percentage applied to the post-discount amount, e.g. `8.5` for 8.5% (`0` if none)
- `shipping` — flat amount added after tax (`0` if none)
- `stated_subtotal`, `stated_discount`, `stated_tax`, `stated_total` — the four figures printed on the invoice, to be checked

## Process

1. Read the invoice JSON from the input file.
2. Implement `validate_invoice(invoice)` as a Python function following **The calculation** below; include the full function in your response.
3. Apply `validate_invoice` to the input invoice.
4. Save its return value as JSON to `output.json` in the current directory.

## The calculation

`validate_invoice(invoice)` computes the four figures in this order, rounding each monetary result to 2 decimal places before using it in the next step:

1. `computed_subtotal` = sum of `quantity * unit_price` over every line item.
2. `computed_discount` = `computed_subtotal * discount_pct / 100`.
3. `computed_tax` = `(computed_subtotal - computed_discount) * tax_rate / 100`.
4. `computed_total` = `computed_subtotal - computed_discount + computed_tax + shipping`.

It then builds `discrepancies` — a list with one entry, **in the fixed order `subtotal`, `discount`, `tax`, `total`**, for each figure whose computed value differs from the stated value:

```
{ "field":    "subtotal" | "discount" | "tax" | "total",
  "stated":   <the figure printed on the invoice>,
  "computed": <the recomputed figure>,
  "delta":    <computed - stated, rounded to 2 decimal places> }
```

A figure matches only when computed equals stated exactly after 2-decimal rounding — there is no tolerance band.

It returns:

```
{ "invoice_id":    <invoice_id>,
  "vendor":        <vendor>,
  "computed":      { "subtotal": ..., "discount": ..., "tax": ..., "total": ... },
  "discrepancies": [ ... ],
  "valid":         <true if discrepancies is empty, else false> }
```

## Output schema

```json
{
  "invoice_id": "string (echo from input; use the filename stem if absent)",
  "vendor": "string (echo from input)",
  "computed": {
    "subtotal": "number, 2 decimal places",
    "discount": "number, 2 decimal places",
    "tax": "number, 2 decimal places",
    "total": "number, 2 decimal places"
  },
  "discrepancies": [
    {
      "field": "subtotal | discount | tax | total",
      "stated": "number — the figure printed on the invoice",
      "computed": "number — the recomputed figure",
      "delta": "number — computed minus stated, 2 decimal places"
    }
  ],
  "valid": "boolean — true when discrepancies is empty"
}
```

## Notes

- `discount_pct` and `tax_rate` are percentages (`8.5` means 8.5%), not fractions.
- Tax is computed on the post-discount amount, never on the gross subtotal. `shipping` is added after tax and is itself untaxed.
- Currency is informational only — do not convert; report amounts in the invoice's own currency.
- Keep `discrepancies` in the fixed order subtotal → discount → tax → total so the report is stable across runs.
- `valid: true` with an empty `discrepancies` list means the invoice's arithmetic is internally consistent — not that the prices themselves are right, only that the figures add up.
