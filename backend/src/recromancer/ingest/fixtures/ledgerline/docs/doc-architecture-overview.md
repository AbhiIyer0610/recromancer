# Ledgerline architecture overview

Ledgerline is the payments reconciliation service. It compares what the payment processor says it settled with what our ledger recorded, and it reports every difference to the finance operations team.

## Components

- **Settlement importer.** A nightly job that reads the processor's settlement files and writes one row per transaction into `settlement_lines`.
- **Matcher.** Pairs each settlement line with a ledger entry by processor reference and amount. Unmatched lines go to an exceptions table.
- **Balance reader.** Computes the expected balance of each account. Balance snapshots are held in a Redis cache so the matcher does not recompute them for every line.
- **Reconciliation API.** A FastAPI service that finance tools call to start a run and to read results.
- **Report writer.** Produces the daily CSV and the exceptions summary.

## Data flow

1. 02:00 UTC: the importer downloads the settlement file and loads it.
2. 02:30 UTC: the matcher runs over the new lines and updates the balance snapshots.
3. 03:00 UTC: the report writer publishes the daily report.

## Storage

Postgres holds all durable state. Amounts are stored as integer minor units (cents) with a currency code. The cache holds only derived data and can be dropped at any time.

## Ownership

Priya Raman owns the service. Questions about matching rules go to her first.
