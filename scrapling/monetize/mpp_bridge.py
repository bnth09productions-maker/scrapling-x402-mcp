"""MPP (Machine Payments Protocol, Stripe/Tempo) doorway alongside x402.

Direct Tempo payments: USDC.e on Tempo mainnet paid straight to the store wallet.
No Stripe account, no facilitator. Verified/broadcast via the public Tempo RPC
through the pympp SDK.

Intents
-------
* ``charge`` — one-shot per call (always, when MPP is enabled). Settles BEFORE
  the data is served.
* ``session`` — streaming payment channel (TIP-1034). One on-chain deposit,
  off-chain vouchers per request, settle/close in the background. Enabled only
  when an operator signing key is configured AND the host is allowed to run
  sessions (primary by default; standbys stay charge-only unless forced).

Supports pympp 0.20+ (``mpp.methods.tempo.server.charge`` / ``.session`` +
``Mpp(...)``) and falls back to the older charge-only API when needed.

Flags / secrets (never log secret values)
-----------------------------------------
* ``MPP_SECRET_KEY`` or ``ADMIN_TOKEN`` — challenge HMAC seed (required for any MPP).
* ``MPP_SESSION_OPERATOR_KEY`` — hex private key for session settle/close, OR
  path via ``MPP_SESSION_OPERATOR_KEY_FILE`` (default
  ``$SCRAPLING_VAULT_DIR/mpp_session_operator.key``).
* ``SCRAPLING_MPP_SESSION`` — ``auto`` (default): on when operator key present
  and host is not a standby; ``1`` force on; ``0`` force off.
* ``SCRAPLING_STANDBY`` — when set, sessions stay off under ``auto`` (charge-only).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import sqlite3
import threading
import time
from os import environ
from typing import Any, Dict, Optional, Set, Tuple

log = logging.getLogger("scrapling.mpp")

TEMPO_USDC = "0x20c000000000000000000000b9537d11c60e8b50"  # USDC.e on Tempo mainnet (chain 4217)
TEMPO_CHAIN_ID = 4217
DECIMALS = 6
OPERATOR_KEY_FILE_DEFAULT = "mpp_session_operator.key"
CHANNEL_DB_NAME = "mpp_session_channels.db"
SESSION_STORE_DB_NAME = "mpp_session_store.db"


class _SqliteStore:
    """Persistent replay-protection store (sync sqlite behind the async Store protocol).

    Used for Tempo **charge** replay. Session channels use pympp's CompareAndSet
    ``SQLiteStore`` instead.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS mpp_kv (k TEXT PRIMARY KEY, v TEXT, ts REAL)")

    def _q(self, sql: str, args: tuple) -> Any:
        with self._lock, sqlite3.connect(self._path) as c:
            cur = c.execute(sql, args)
            return cur.fetchone(), cur.rowcount

    async def get(self, key: str) -> Any:
        row, _ = self._q("SELECT v FROM mpp_kv WHERE k=?", (key,))
        return row[0] if row else None

    async def put(self, key: str, value: Any) -> None:
        self._q("INSERT OR REPLACE INTO mpp_kv (k, v, ts) VALUES (?,?,?)", (key, str(value), time.time()))

    async def delete(self, key: str) -> None:
        self._q("DELETE FROM mpp_kv WHERE k=?", (key,))

    async def put_if_absent(self, key: str, value: Any) -> bool:
        _, n = self._q("INSERT OR IGNORE INTO mpp_kv (k, v, ts) VALUES (?,?,?)", (key, str(value), time.time()))
        return n == 1


class _ChannelIdBook:
    """Remember session channel IDs so close-supervision can poll them."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        with sqlite3.connect(path) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS channels ("
                "channel_id TEXT PRIMARY KEY, first_seen REAL, last_seen REAL, status TEXT)"
            )

    def add(self, channel_id: str) -> None:
        if not channel_id:
            return
        now = time.time()
        with self._lock, sqlite3.connect(self._path) as c:
            c.execute(
                "INSERT INTO channels(channel_id, first_seen, last_seen, status) VALUES(?,?,?,?) "
                "ON CONFLICT(channel_id) DO UPDATE SET last_seen=excluded.last_seen, status='open'",
                (channel_id, now, now, "open"),
            )

    def drop(self, channel_id: str) -> None:
        with self._lock, sqlite3.connect(self._path) as c:
            c.execute(
                "UPDATE channels SET status=?, last_seen=? WHERE channel_id=?",
                ("closed", time.time(), channel_id),
            )

    def open_ids(self) -> Set[str]:
        with self._lock, sqlite3.connect(self._path) as c:
            rows = c.execute(
                "SELECT channel_id FROM channels WHERE status IS NULL OR status='open'"
            ).fetchall()
        return {r[0] for r in rows}


def _sessions_flag_enabled() -> bool:
    raw = (environ.get("SCRAPLING_MPP_SESSION") or "auto").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if raw in ("1", "true", "on", "yes"):
        return True
    # auto: primary only
    if environ.get("SCRAPLING_STANDBY"):
        return False
    return True


def _load_operator_key() -> Optional[str]:
    """Return a hex private key string, or None. Never log the value."""
    direct = (environ.get("MPP_SESSION_OPERATOR_KEY") or "").strip()
    if direct:
        return direct
    vault = environ.get("SCRAPLING_VAULT_DIR", "/collected_data")
    path = (environ.get("MPP_SESSION_OPERATOR_KEY_FILE") or "").strip() or os.path.join(
        vault, OPERATOR_KEY_FILE_DEFAULT
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
        return key or None
    except FileNotFoundError:
        return None
    except Exception as err:
        log.warning("MPP session operator key unreadable: %s", err)
        return None


class MppDoor:
    def __init__(self, recipient: str, realm: str = "scraplingdata.xyz") -> None:
        self.enabled = False
        self.sessions_enabled = False
        self.recipient = recipient
        self.realm = realm
        self.operator_address: Optional[str] = None
        self._mpp = None
        self._api = None  # "v20" | "legacy"
        self._account = None
        self._session_store = None
        self._channel_book: Optional[_ChannelIdBook] = None
        self._supervise_stop = threading.Event()
        self._supervise_thread: Optional[threading.Thread] = None
        self._rpc = environ.get("TEMPO_RPC_URL", "https://rpc.tempo.xyz")
        try:
            seed = environ.get("MPP_SECRET_KEY") or environ.get("ADMIN_TOKEN") or ""
            if not seed:
                log.warning("MPP disabled: no MPP_SECRET_KEY/ADMIN_TOKEN")
                return
            secret = hmac.new(seed.encode(), b"scrapling-mpp-challenge-v1", hashlib.sha256).hexdigest()
            vault = environ.get("SCRAPLING_VAULT_DIR", "/collected_data")
            charge_store = _SqliteStore(f"{vault}/mpp_replay.db")

            # Prefer pympp 0.20+ server API (box/uv.lock currently 0.20.0).
            try:
                from mpp.server import Mpp
                from mpp.methods.tempo import server as tempo

                methods: list = []
                methods.extend(
                    tempo.charge(
                        currencies=[TEMPO_USDC],
                        recipient=recipient,
                        decimals=DECIMALS,
                        rpc_url=self._rpc,
                        chain_id=TEMPO_CHAIN_ID,
                        store=charge_store,
                    )
                )

                want_sessions = _sessions_flag_enabled()
                op_key = _load_operator_key() if want_sessions else None
                if want_sessions and op_key:
                    try:
                        self._try_add_session(methods, tempo, recipient, vault, op_key)
                    except Exception as sess_err:
                        # Never let session setup kill charge / x402.
                        log.warning("MPP sessions not enabled: %s", sess_err)
                        self.sessions_enabled = False
                        self._account = None
                        self._session_store = None
                        self._channel_book = None
                        methods[:] = [m for m in methods if getattr(m, "intent", None) != "session"]

                self._mpp = Mpp(
                    methods=methods,
                    realm=realm,
                    secret_key=secret,
                )
                self._api = "v20"
            except Exception as v20_err:
                # Legacy: charge-only. Sessions require pympp 0.20+.
                from mpp.server import Mpp
                from mpp.methods.tempo import tempo, ChargeIntent  # type: ignore

                if not hasattr(Mpp, "create"):
                    raise v20_err
                self._mpp = Mpp.create(
                    method=tempo(
                        currency=TEMPO_USDC,
                        recipient=recipient,
                        decimals=DECIMALS,
                        rpc_url=self._rpc,
                        intents={"charge": ChargeIntent(chain_id=TEMPO_CHAIN_ID, store=charge_store)},
                    ),
                    realm=realm,
                    secret_key=secret,
                )
                self._api = "legacy"
                log.info("MPP using legacy pympp tempo() API (%s)", v20_err)
            self.enabled = True
            if self.sessions_enabled:
                self._start_supervise_loop()
                log.info(
                    "MPP sessions enabled (operator=%s…%s); charge+session in WWW-Authenticate",
                    (self.operator_address or "")[:6],
                    (self.operator_address or "")[-4:],
                )
        except Exception as err:  # never break x402 because of MPP
            log.warning("MPP disabled: %s", err)

    def _try_add_session(self, methods: list, tempo: Any, recipient: str, vault: str, op_key: str) -> None:
        from mpp.methods.tempo.account import TempoAccount
        from mpp.methods.tempo.session.server import SettlementSchedule
        from mpp.stores.sqlite import SQLiteStore

        key = op_key if op_key.startswith("0x") else "0x" + op_key
        account = TempoAccount.from_key(key)
        self._account = account
        self.operator_address = str(account.address)

        session_store = asyncio.run(SQLiteStore.create(os.path.join(vault, SESSION_STORE_DB_NAME)))
        self._session_store = session_store
        self._channel_book = _ChannelIdBook(os.path.join(vault, CHANNEL_DB_NAME))

        def _on_payment_success(event: Any = None, **kwargs: Any) -> None:
            try:
                receipt = getattr(event, "receipt", None) if event is not None else kwargs.get("receipt")
                if receipt is None and isinstance(event, dict):
                    receipt = event.get("receipt")
                ref = None
                if receipt is not None:
                    ref = getattr(receipt, "reference", None) or getattr(receipt, "channel_id", None)
                if not ref and isinstance(event, dict):
                    ref = event.get("reference") or event.get("channel_id")
                if ref and self._channel_book is not None:
                    self._channel_book.add(str(ref))
            except Exception as err:
                log.warning("MPP session channel bookkeeping failed: %s", err)

        methods.append(
            tempo.session(
                account=account,
                recipient=recipient,
                currency=TEMPO_USDC,
                decimals=DECIMALS,
                chain_id=TEMPO_CHAIN_ID,
                rpc_url=self._rpc,
                store=session_store,
                settlement_schedule=SettlementSchedule(
                    amount="1",  # settle after ~$1 accepted spend
                    interval_ms=60_000,
                ),
                unit_type="request",
                reusable_challenges=True,
                charge_on_acceptance=True,
                fee_payer=True,  # operator pays settle/close gas
                on_payment_success=_on_payment_success,
            )
        )
        self.sessions_enabled = True

    def _start_supervise_loop(self) -> None:
        if self._supervise_thread and self._supervise_thread.is_alive():
            return
        self._supervise_stop.clear()

        def _loop() -> None:
            previous_block: Optional[int] = None
            while not self._supervise_stop.wait(60):
                try:
                    previous_block_box: Dict[str, Any] = {"b": previous_block}
                    self._run_supervise_once(previous_block_box)
                    previous_block = previous_block_box.get("b", previous_block)
                except Exception as err:
                    log.warning("MPP session close supervision failed: %s", err)

        self._supervise_thread = threading.Thread(target=_loop, name="mpp-session-supervise", daemon=True)
        self._supervise_thread.start()

    def _run_supervise_once(self, previous_block_box: Dict[str, Any]) -> None:
        if not self.sessions_enabled or self._account is None or self._session_store is None:
            return
        if self._channel_book is None:
            return
        ids = self._channel_book.open_ids()
        if not ids:
            return
        from mpp.methods.tempo.session.server import supervise_closes

        async def _go() -> Any:
            return await supervise_closes(
                channel_ids=list(ids),
                store=self._session_store,
                rpc_url=self._rpc,
                account=self._account,
                chain_id=TEMPO_CHAIN_ID,
                previous_block=previous_block_box.get("b"),
            )

        result = self._run(_go())
        if result is None:
            return
        if getattr(result, "complete", False):
            previous_block_box["b"] = getattr(result, "block", previous_block_box.get("b"))
        for item in getattr(result, "channels", []) or []:
            outcome = getattr(item, "outcome", None)
            channel_id = getattr(item, "channel_id", None)
            if outcome in ("closed", "finalized", "missing", "unrecovered") and channel_id:
                self._channel_book.drop(str(channel_id))

    def stop(self) -> None:
        self._supervise_stop.set()

    @staticmethod
    def _amount(usd: float) -> str:
        return f"{usd:.6f}".rstrip("0").rstrip(".") or "0"

    def _run(self, coro: Any) -> Any:
        box: Dict[str, Any] = {}

        def _t() -> None:
            try:
                box["r"] = asyncio.run(coro)
            except BaseException as e:  # noqa: BLE001
                box["e"] = e

        th = threading.Thread(target=_t, daemon=True)
        th.start()
        th.join(60)
        if "e" in box:
            raise box["e"]
        return box.get("r")

    def _request(self, authorization: Optional[str] = None, url: Optional[str] = None):
        from mpp.server import Request

        headers = {}
        if authorization:
            headers["Authorization"] = authorization
        return Request(method="GET", url=url or f"https://{self.realm}/", headers=headers)

    def _handler(self, usd: float, description: Optional[str] = None):
        """Return a pympp handler: charge alone, or charge+session composed."""
        assert self._mpp is not None
        desc = (description or "")[:200] or None
        amount = self._amount(usd)
        charge_h = self._mpp.charge(amount=amount, description=desc)
        if not self.sessions_enabled:
            return charge_h
        from mpp.server.compose import compose

        session_h = self._mpp.session(amount=amount, unit_type="request", description=desc)
        return compose(charge_h, session_h)

    def challenge_header(self, usd: float, description: Optional[str] = None) -> Optional[str]:
        if not self.enabled or usd <= 0 or self._mpp is None:
            return None
        try:
            if self._api == "v20":
                res = self._run(self._handler(usd, description)(self._request()))
                ch = getattr(res, "challenge", None)
                if ch is None:
                    return None
                headers = getattr(ch, "headers", None) or {}
                return headers.get("www-authenticate") or headers.get("WWW-Authenticate")
            ch = self._run(
                self._mpp.charge(
                    authorization=None,
                    amount=self._amount(usd),
                    description=(description or "")[:200] or None,
                )
            )
            return ch.to_www_authenticate(self.realm)
        except Exception as err:
            log.warning("MPP challenge failed: %s", err)
            return None

    @staticmethod
    def is_mpp(authorization: Optional[str]) -> bool:
        return bool(authorization) and authorization.strip().lower().startswith("payment ")

    def verify(
        self, authorization: str, usd: float, description: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """Returns (paid, info, error). Charge: funds already moved. Session: voucher accepted."""
        if not self.enabled or self._mpp is None:
            return False, {}, "MPP payments are not enabled on this server."
        try:
            if self._api == "v20":
                res = self._run(self._handler(usd, description)(self._request(authorization)))
                if getattr(res, "status", None) != 200:
                    return False, {}, "MPP credential rejected (wrong amount, expired, replayed or unpaid)."
                credential = getattr(res, "credential", None)
                receipt = getattr(res, "receipt", None)
            else:
                from mpp import Challenge

                res = self._run(
                    self._mpp.charge(
                        authorization=authorization,
                        amount=self._amount(usd),
                        description=(description or "")[:200] or None,
                    )
                )
                if isinstance(res, Challenge):
                    return False, {}, "MPP credential rejected (wrong amount, expired, replayed or unpaid)."
                credential, receipt = res
        except Exception as err:
            return False, {}, f"MPP verification failed: {err}"
        ref = str(getattr(receipt, "reference", "") or "")
        payer = str(getattr(credential, "source", "") or "0xUnknown")
        intent = str(getattr(receipt, "intent", None) or getattr(credential, "intent", None) or "charge")
        if intent == "session" and ref and self._channel_book is not None:
            try:
                self._channel_book.add(ref)
            except Exception:
                pass
        try:
            rhdr = receipt.to_payment_receipt() if hasattr(receipt, "to_payment_receipt") else None
        except Exception:
            rhdr = None
        scheme = "mpp-tempo-session" if intent == "session" else "mpp-tempo"
        return (
            True,
            {
                "tx_hash": ref,
                "payer": payer,
                "receipt_header": rhdr,
                "intent": intent,
                "settlement_scheme": scheme,
            },
            None,
        )

    def discovery_protocols(self) -> list:
        if not self.enabled:
            return []
        out = [{"mpp": {"method": "tempo", "intent": "charge", "currency": TEMPO_USDC}}]
        if self.sessions_enabled:
            out.append({"mpp": {"method": "tempo", "intent": "session", "currency": TEMPO_USDC}})
        return out

    def challenge_note(self) -> str:
        if self.sessions_enabled:
            return (
                "Also payable via MPP (Machine Payments Protocol): USDC.e on Tempo. "
                "WWW-Authenticate offers intent=charge (one-shot) and intent=session "
                "(deposit once, vouchers per call, settle/close later). "
                "Retry with Authorization: Payment <credential>."
            )
        return (
            "Also payable via MPP (Machine Payments Protocol): USDC.e on Tempo, "
            "challenge in WWW-Authenticate; retry with Authorization: Payment <credential>."
        )
