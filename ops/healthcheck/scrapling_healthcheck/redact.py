"""Strip secrets, IPs, and wallet-shaped strings from reports."""
from __future__ import annotations

import re

_WALLET = re.compile(r"0x[a-fA-F0-9]{20,}")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b")
_SECRET = re.compile(r"(?i)(bearer\s+|sk_scrapling_|api[_-]?key=)[A-Za-z0-9._\-]+")


def redact(value: str) -> str:
    text = _SECRET.sub(r"\1[redacted]", value)
    text = _WALLET.sub("0x[redacted]", text)
    text = _IPV4.sub("[ip]", text)
    text = _IPV6.sub("[ip]", text)
    return text
