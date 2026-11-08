# Health checker acceptance fixes

Code changes are in this package only. The store gateway and its prices were not edited. Repeat-buyer work is not in this package. The counterparty row in `config/healthcheck.json` is still `0.004`.

## What changed

1. Optional local paths that are unset no longer produce overall `ok` / exit 0. The report is overall `incomplete` / exit 3, and each public check that passed is still `pass`. A path that is required, or a path that is set and missing, is `missing` and the run exits 1.
2. Host matching is the exact hostname or a dotted subdomain, plus the same https port. `evilscraplingdata.xyz` is rejected. Redirects off that host, scheme, or port are not followed. Secret headers, including `PAYMENT-SIGNATURE`, are not sent to the redirect target.
3. The outbox records failure, recovery, and other fingerprint changes. The webhook posts failures only. Recovery is sent only when `SCRAPLING_HEALTH_WEBHOOK_RECOVERY=1`. A known stall stays `watch` and is not re-alerted; aging past the window is a failure event. Auth is a header name plus value from the environment, or nothing. No format is assumed and no chat URL is built in.

The scheduler is `python3 -m scrapling_healthcheck daemon`. It waits 4 hours between runs, handles SIGTERM/SIGINT, and uses a pid file plus the existing run lock. `--detach` does not come back after reboot. This box has no crontab and no systemd user session.

## Tests

11 unittest cases, no network. They cover the lookalike host and redirect header drop, incomplete versus required-missing evidence, the webhook filter, stall suppression versus worsening, and the daemon wait/stop path.

## Install

Unzip over `/workspace/store-health-checker`. Run `once` before `daemon`. Leave the existing AI health routine in place until the daemon is running and a failure post has been verified. See `install/INSTALL.md`.
