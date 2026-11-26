# Ledgerline handoff notes

Written for the people who take over the service. My last day is 2026-10-16.

## State of the service

- Matching, streaming batches, idempotency keys and rate ids are all in production.
- Nightly jobs run from systemd timers on `ledger-a`; see the deployment runbook.

## Decisions worth knowing

- The balance cache was removed because invalidation raced with the import. See PR 142.
- Posting endpoints require an `Idempotency-Key` header. See issue 233.
- Converted amounts carry a rate id so reports are reproducible. See the RFC review notes and PR 163.

## Open work

- Batch the idempotency purge (issue 270).
- Add the month-end replay to the release checklist.

## Who to ask

The three reviewers on recent changes know the code: `tomas-okafor`, `mei-lindqvist` and `arjun-pale`.
