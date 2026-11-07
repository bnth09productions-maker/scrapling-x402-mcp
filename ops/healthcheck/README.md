# Scrapling health checker

Standalone checks for the public store and optional local evidence. A normal run does not call a model, does not pay, and does not send mail or chat. Reports stay on disk. An HTTPS webhook runs only if `SCRAPLING_HEALTH_WEBHOOK_URL` is set in the process environment.

This folder does not modify the gateway. The current AI health routine can keep running until this checker is installed and compared.

## Layout

- `scrapling_healthcheck/` program
- `config/healthcheck.json` public routes and prices verified 2026-11-07
- `install/` cron, systemd user timer, and Windows task examples, all disabled
- `tests/test_healthcheck.py` offline tests
- `var/` created at runtime (state, reports, logs, outbox)

## Commands

From this directory:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 -m scrapling_healthcheck once --config config/healthcheck.json
```

Exit `0` means no `fail` or `missing` checks. `watch` and `not_configured` do not fail the run. Exit `1` means a real failure or missing required evidence. Exit `2` means a bad config, a lock held by another run, or an internal error.

## What a pass is not

A known demand_radar or sync gap inside its configured window is `watch`. That is not a healthy pass and it is not suppressed: crossing `known_stall_max_age_seconds` or `known_gap_seconds` becomes `fail`. If a local path is unset, the check is `not_configured`. If a path is set and the file or schema is absent, the check is `missing` and the run fails. Row counts are not treated as conversion.

## Alerts

New failure, recovery, and other fingerprint changes append to `var/outbox/events.jsonl`. The same fingerprint does not alert again. Without the webhook env var the outbox says local only. No external chat is delivered until that hook is connected. The URL must be `https`. Retries are bounded. The body is the event id, kind, check, summary, and time. Credentials are not read or sent.

## Install on the box

Do not install into the git checkout. Unzip the artifact so the program root is `/workspace/store-health-checker`, then run the once command above. Leave cron and systemd disabled until that run looks right. See `install/INSTALL.md`.
