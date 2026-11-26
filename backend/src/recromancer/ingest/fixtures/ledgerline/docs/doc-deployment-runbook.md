# Ledgerline deployment runbook

Ledgerline runs on two virtual machines, `ledger-a` and `ledger-b`. Each runs the same Docker Compose project. Scheduled work is driven by systemd timers on the host. There is no cluster scheduler.

## Layout

| Host | Role |
|---|---|
| ledger-a | API, importer timer, report timer |
| ledger-b | API (second instance), standby for timers |

Both hosts keep the compose project in `/opt/ledgerline`. The compose file defines the `api` and `worker` services. Postgres is managed separately and is not part of the compose project.

## Normal deploy

1. Tag the release and wait for the image build.
2. On `ledger-b`: `cd /opt/ledgerline && docker compose pull && docker compose up -d`.
3. Check `/readyz` on `ledger-b` and watch the error rate for five minutes.
4. Repeat on `ledger-a`.

Doing `ledger-b` first keeps one healthy instance behind the load balancer at all times.

## Scheduled jobs

Timers live in `/etc/systemd/system/` on `ledger-a`.

| Unit | Schedule (UTC) | Command |
|---|---|---|
| ledgerline-import.timer | 02:00 daily | `docker compose run --rm worker import` |
| ledgerline-match.timer | 02:30 daily | `docker compose run --rm worker match` |
| ledgerline-report.timer | 03:00 daily | `docker compose run --rm worker report` |
| ledgerline-purge.timer | 04:00 daily | `docker compose run --rm worker purge-keys` |

Each `.service` file sets `Type=oneshot` and `TimeoutStartSec=3h`. Check status with `systemctl list-timers 'ledgerline-*'` and logs with `journalctl -u ledgerline-import.service --since today`.

## Moving timers to ledger-b

If `ledger-a` is lost:

1. Copy the four unit pairs to `ledger-b`.
2. `systemctl daemon-reload && systemctl enable --now ledgerline-*.timer`.
3. Confirm with `systemctl list-timers`.

Do not enable the timers on both hosts. The importer takes a database advisory lock, but two hosts running at once makes logs hard to read.

## Rollback

1. `cd /opt/ledgerline && git checkout <previous tag> -- compose.yaml`.
2. `docker compose up -d` on `ledger-b`, check, then `ledger-a`.
3. Database migrations are forward only. If a migration must be undone, write a new migration; never edit an applied one.

## Health checks

- `/healthz` answers 200 if the process is up.
- `/readyz` answers 200 if Postgres is reachable and migrations are current.

## Backups

Postgres is backed up nightly at 05:00 UTC by the database host, with point-in-time recovery for 7 days. Restore drills run quarterly. The idempotency table is part of the backup; it is small.

## Secrets

Secrets are read from `/etc/ledgerline/env` (mode 0600, owned by root) and passed to compose with `--env-file`. They are never baked into images or committed.

## Troubleshooting

- **Import did not run.** Check `systemctl status ledgerline-import.timer`. If the timer is active, read the service journal.
- **Report is late.** The match step may still be running. Look for the advisory lock in `pg_locks` before starting another run.
- **Container restarts in a loop.** `docker compose logs --tail 200 api`. Most often it is a missing variable in the env file.
- **Disk full on a host.** `docker system df`, then `docker image prune` for unused images. Old images are the usual cause.

## Contacts

Ownership is described in the handoff notes. Page the payments team channel for urgent issues.
