# Install and uninstall

The schedule in this artifact is disabled. Do not enable cron, systemd, or Task Scheduler until `once` has been compared with the current health routine.

## Box install (isolated directory)

```bash
mkdir -p /workspace/store-health-checker
# unzip the artifact so these files land in that directory, not in the git checkout
cd /workspace/store-health-checker
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 -m scrapling_healthcheck once --config config/healthcheck.json
```

Optional local evidence, edited only in the installed config, not in git:

- `local.sync_evidence.path`: JSON file `{"last_ok":"2026-11-07T18:21:00Z"}` or `{"last_ok_unix": 1700000000}`
- `local.demand_radar.path`: read-only path to `demand_radar.db` (`demand.ts`)
- `local.request_log.path`: read-only path to the request log (`request_log.ts`, `request_log.status`)

`fresh_seconds` is a pass. Up to `known_gap_seconds` or `known_stall_max_age_seconds` is `watch` (the parked stall). Older than that is `fail`. Unset paths stay `not_configured`.

Standby MPP that advertises charge only: set `mpp.intents` to `["charge"]`. The public primary config expects `charge` and `session`.

## Linux cron every 4 hours (do not install yet)

`install/scrapling-healthcheck.cron.disabled` is not a live crontab. After the once command is accepted:

```bash
crontab install/scrapling-healthcheck.cron.disabled
```

Remove it later with `crontab -e` and delete that line.

## systemd user timer every 4 hours (do not enable yet)

```bash
mkdir -p ~/.config/systemd/user
cp install/systemd/scrapling-healthcheck.service ~/.config/systemd/user/
cp install/systemd/scrapling-healthcheck.timer ~/.config/systemd/user/
# only after verification:
# systemctl --user daemon-reload
# systemctl --user enable --now scrapling-healthcheck.timer
```

Uninstall later:

```bash
systemctl --user disable --now scrapling-healthcheck.timer || true
rm -f ~/.config/systemd/user/scrapling-healthcheck.service ~/.config/systemd/user/scrapling-healthcheck.timer
systemctl --user daemon-reload
```

## Windows Task Scheduler

`install/windows/scrapling-healthcheck-task.xml` has `Enabled` false. Do not import it until the once command is accepted. The command is `python.exe -m scrapling_healthcheck once --config config\healthcheck.json` with the working directory set to the install folder.

## Uninstall the program

```bash
# disable cron and systemd first, using the commands above
rm -rf /workspace/store-health-checker
```

That removes local reports and state for this checker only. It does not touch the gateway.
