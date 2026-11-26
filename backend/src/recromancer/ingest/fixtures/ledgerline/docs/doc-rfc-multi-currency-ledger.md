# RFC: multi-currency ledger

Status: draft, circulated for review. Review notes are recorded as a separate item.

## Summary

Ledgerline today reconciles in a single currency per account. Finance wants one consolidated view in EUR (€) across accounts held in USD, GBP and JPY. This RFC proposes how amounts and exchange rates are represented in the ledger.

## Motivation

- Finance currently exports each currency separately and converts by hand in a spreadsheet.
- Reports for a group of accounts need one total. The sandbox account `café-float` holds the EUR test data used in the examples below.
- Treasury updates rates several times a day and wants reports to reflect "current" rates.

## Proposal

Convert at read time.

1. The ledger keeps storing each entry in its original currency.
2. When a report or balance is requested, the service looks up the latest rate for the currency pair and converts every entry on the fly.
3. For speed, the converted value is stored on the row as a `double precision` column (`amount_reporting`) and refreshed whenever the rate changes.
4. Reports read `amount_reporting` directly.

Example row after the proposal:

| id | amount | currency | amount_reporting |
|---|---|---|---|
| 88121 | 1250.00 | USD | 1153.4615384615 |
| 88122 | 90.10 | GBP | 106.1237 |

## Schema changes

```sql
ALTER TABLE ledger_entries ADD COLUMN amount_reporting double precision;
ALTER TABLE ledger_entries ADD COLUMN reporting_rate_at timestamptz;
```

A background job refreshes `amount_reporting` every time treasury publishes a rate update.

## Benefits

- Reports always use the latest rate.
- No change to how entries are written.
- Reads are a single column lookup.

## Costs and risks

- The refresh job touches many rows after every rate update.
- Floating point rounding differs by database version and by query plan, so totals can differ in the last digits.
- Historical reports change when rates change.

## Alternatives

**Option A: convert at write time.** Fix a rate when the entry is posted and store the converted amount. Simple, but a wrong rate is baked in permanently.

**Option B: reference a rate table.** Store the original amount and a pointer to an immutable rate record, and derive conversions when needed. More joins, but nothing is overwritten.

This draft recommends the read-time approach for its simplicity.

## Open questions

1. Which rate source does treasury want as the reference?
2. Does the reporting currency vary per workspace?
3. How long do we keep superseded rates?

## Rollout

Behind a feature flag per account group. Backfill `amount_reporting` for the last 13 months. Compare with the finance spreadsheet for one month-end before switching reports over.

## Review

Comments are welcome until 2026-03-06. Please attach your notes as a review item and reference this document.
