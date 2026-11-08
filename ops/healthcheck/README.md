# Scrapling health checker

Standalone checks for the public store and optional local evidence. A normal run does not call a model, does not pay, and does not send mail or chat. Reports stay on disk. An HTTPS webhook runs only if `SCRAPLING_HEALTH_WEBHOOK_URL` is set in the process environment, and by default it posts failure events only.

This folder does not modify the gateway or its prices. The current AI health routine stays in place until this checker is on a schedule and failure delivery to chat has been verified. This package does not start that schedule and does not send chat.

## Layout

- `scrapling_healthcheck/` program
- `config/healthcheck.json` public routes. Counterparty stays `$0.004` until the live price cut is verified.
- `install/` disabled cron, systemd, and Windows examples. The box has no crontab and no systemd user session, so the scheduler on that host is the stdlib daemon.
- `tests/test_healthcheck.py` offline tests
- `var/` created at runtime (state, reports, logs, outbox)

## Commands

From this directory:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 -m scrapling_healthcheck once --config config/healthcheck.json
PYTHONPATH=. python3 -m scrapling_healthcheck daemon --config config/healthcheck.json
PYTHONPATH=. python3 -m scrapling_healthcheck stop --config config/healthcheck.json
```

`daemon --detach` backgrounds the process and writes `var/state/daemon.pid`. Stopping sends SIGTERM, waits, then one SIGKILL. Neither foreground nor `--detach` comes back after a reboot. This box has no crontab and no `systemd --user`, so a background launch is not boot persistence.

## Exit status

Each check keeps its own status. Public passes stay `pass` even when the overall result is not ok.

- Exit `0`, overall `ok`: every check passed.
- Exit `1`, overall `fail`: a check failed, or required evidence is absent (`missing`).
- Exit `2`: bad config, lock held by another run, or an internal error.
- Exit `3`, overall `incomplete`: nothing failed, and at least one optional local path is unset. That is a coverage gap, not a healthy pass.
- Exit `4`, overall `degraded`: nothing failed and nothing is unconfigured, but a known stall is inside its window. That is not a new outage. Crossing the window is exit `1`. Do not page on exit 4. Page on exit 1 and on webhook failure events.

## What a pass is not

A known demand_radar or sync gap inside its configured window is `watch`. The same fingerprint is not alerted again. Crossing `known_stall_max_age_seconds` or `known_gap_seconds` becomes `fail` and is not suppressed. If a local path is unset and `required` is not true, the check is `not_configured`. If `required` is true, or a path is set and the file or schema is absent, the check is `missing` and the run fails. Row counts are not treated as conversion.

## Alerts

Fingerprint changes append to `var/outbox/events.jsonl`. The same fingerprint does not append again. A failure that recovers and fails again is a new event. The outbox keeps failure, recovery, and other changes. The webhook posts `failure` only, unless `SCRAPLING_HEALTH_WEBHOOK_RECOVERY=1`. Other kinds stay local. The URL must be `https`. Three attempts, then stop. No auth header is added unless both `SCRAPLING_HEALTH_WEBHOOK_AUTH_HEADER` and `SCRAPLING_HEALTH_WEBHOOK_AUTH_VALUE` are set. The checker does not assume Bearer or any other format, and it has no built-in chat endpoint.

## Install on the box

Do not install into the git checkout. Unzip the artifact so the program root is `/workspace/store-health-checker`. See `install/INSTALL.md`.
