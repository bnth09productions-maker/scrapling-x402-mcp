# Install and uninstall

Replace the installed tree at `/workspace/store-health-checker` with this artifact. Do not copy it into the gateway tree. Do not start it from the git checkout. The current AI health routine stays until this daemon is actually running and a failure webhook has been seen to deliver.

## Box install

```bash
rm -rf /workspace/store-health-checker
mkdir -p /workspace/store-health-checker
# unzip so these files land in that directory
cd /workspace/store-health-checker
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 -m scrapling_healthcheck once --config config/healthcheck.json
```

With the shipped config, public checks can pass while local paths are unset. That run is overall `incomplete` and exit `3`. The report still lists each public check as `pass`. Exit `3` means the local evidence was not configured. It does not mean the public store failed.

Optional local evidence, edited only in the installed config:

- `local.sync_evidence.path`: JSON file `{"last_ok":"2026-11-07T18:21:00Z"}` or `{"last_ok_unix": 1700000000}`
- `local.demand_radar.path`: read-only path to `demand_radar.db` (`demand.ts`)
- `local.request_log.path`: read-only path to the request log (`request_log.ts`, `request_log.status`)

`fresh_seconds` is a pass. Up to `known_gap_seconds` or `known_stall_max_age_seconds` is `watch`. Older than that is `fail`. An unset path is `not_configured` (coverage gap, exit 3) unless that block has `"required": true`, in which case a missing path is `missing` and the run exits 1. A path that is set but whose file is absent is `missing` and exits 1.

Standby MPP that advertises charge only: set `mpp.intents` to `["charge"]`. The public primary config expects `charge` and `session`.

## Counterparty price, one row

`paid_402` id `counterparty` stays `0.004` until a live unpaid `GET /v1/check/counterparty` shows `priceUsd` `0.002`. After that verification, change only this value:

`config/healthcheck.json` → `paid_402` → `{"id": "counterparty", ... "usd": 0.004}` → `"usd": 0.002`

Leave every other price in that file unchanged.

## Daemon, every 4 hours

The box has no crontab and no systemd user session. Cron and systemd files in this folder are examples for a different host. They are not installed here and they are not a reboot hook.

```bash
cd /workspace/store-health-checker
PYTHONPATH=. python3 -m scrapling_healthcheck daemon --config config/healthcheck.json
```

That process runs one check, then waits the full `interval_seconds` (14400). It does not burst a backlog. The interval cannot be set below 60 seconds. SIGTERM or SIGINT stops it after the current sleep or the current run. The pid file is `var/state/daemon.pid`. The run lock is `var/state/run.lock`.

Background start, still not reboot-persistent:

```bash
PYTHONPATH=. python3 -m scrapling_healthcheck daemon --detach --config config/healthcheck.json
PYTHONPATH=. python3 -m scrapling_healthcheck stop --config config/healthcheck.json
```

`--detach` forks, redirects its output into `var/logs/daemon.log`, and exits the parent. A reboot kills it. Nothing in this package registers a boot service on the box. Logs rotate at 1 MB, five backups, for `daemon.log` and for `var/logs/healthcheck.log`.

## Webhook

Leave it unset until the endpoint and its auth are known. There is no built-in chat URL.

- `SCRAPLING_HEALTH_WEBHOOK_URL` must be `https`. Failures are posted. Recovery is posted only when `SCRAPLING_HEALTH_WEBHOOK_RECOVERY=1`. Other status changes stay in `var/outbox/events.jsonl`.
- Set `SCRAPLING_HEALTH_WEBHOOK_AUTH_HEADER` and `SCRAPLING_HEALTH_WEBHOOK_AUTH_VALUE` together if the endpoint needs a header. One without the other does not send, and the checker does not guess Bearer or any other scheme.
- Three tries, then stop. No signup and no send during install.

## Other hosts

`install/scrapling-healthcheck.cron.disabled` and `install/systemd/` are disabled examples. Do not install them on the box. `install/windows/scrapling-healthcheck-task.xml` has `Enabled` false.

## Uninstall

```bash
cd /workspace/store-health-checker
PYTHONPATH=. python3 -m scrapling_healthcheck stop --config config/healthcheck.json
rm -rf /workspace/store-health-checker
```

That removes this checker's reports and state. It does not touch the gateway.
