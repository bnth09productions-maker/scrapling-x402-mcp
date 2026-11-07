Prefer apply_counterparty_price.py. It preflights all six anchors before it writes, and a failed preflight leaves every target byte unchanged. Do not apply these diffs one file at a time. A partial tree is a mixed state, and the script will refuse to finish it.

patches/gateway.py.diff applies only to gateway.py whose sha256 is
1d360fc0224d016b01ab54c7400d90fcf34b4d72d822038ca03c5418ce91dba9
(the branch file after the OpenAPI alias and sample checks, before this price line).
Reject the diff if the context does not match. Do not use a copy of the 2026-11-06 standby gateway.

The other diffs are context from that standby snapshot. If patch reports a reject or a fuzz, stop and use the apply script. Do not copy the snapshot files over the live tree.
