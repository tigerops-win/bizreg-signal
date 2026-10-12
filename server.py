#!/usr/bin/env python3
"""
BizRegSignal by Tiger Operations.

Machine-readable, per-call US business-registration signals delivered through an
x402 payment flow on Base. Module: bizreg-signal/1.0
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MODULE = "bizreg-signal/1.0"
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8080"))

DATA_DIR = Path("data")
REGISTRATIONS_FILE = DATA_DIR / "registrations.json"
REDEEMED_FILE = DATA_DIR / "redeemed.json"
SALES_FILE = DATA_DIR / "bizreg_sales.jsonl"

NETWORK = "eip155:8453"
USDC_ADDRESS = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
DEFAULT_RECEIVING_ADDRESS = "0xAF7B70D8487EE6193701597E67f56A5902d23913"
RECEIVING_ADDRESS = os.getenv(
    "BIZREGSIGNAL_RECEIVING_ADDRESS", DEFAULT_RECEIVING_ADDRESS
).strip()
SALES_ENABLED = bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", RECEIVING_ADDRESS))
PRICE_ATOMIC = 50_000
PRICE_USD = 0.05
MAX_PAYMENT_AGE = timedelta(days=30)

RATE_LIMIT = 120
RATE_WINDOW_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 15

FILE_LOCK = threading.Lock()
RATE_LOCK = threading.Lock()
REQUESTS_BY_IP: dict[str, deque[float]] = defaultdict(deque)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def registrations() -> list[dict[str, Any]]:
    value = load_json(REGISTRATIONS_FILE, [])
    return value if isinstance(value, list) else []


def parse_registration_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def find_registrations(
    state: str, days: int, category: str, limit: int
) -> list[dict[str, Any]]:
    state = state.strip().upper()
    category = category.strip().casefold()
    cutoff = utc_now().date() - timedelta(days=max(0, days))
    matches: list[dict[str, Any]] = []

    for item in registrations():
        registered = parse_registration_date(item.get("registration_date"))
        if registered is None or registered < cutoff:
            continue
        if state and str(item.get("state", "")).upper() != state:
            continue
        if category and category not in str(item.get("category", "")).casefold():
            continue
        matches.append(item)

    matches.sort(
        key=lambda item: (
            str(item.get("registration_date", "")),
            str(item.get("entity_name", "")),
        ),
        reverse=True,
    )
    return matches[: max(1, min(limit, 100))]


def find_company(name: str, state: str) -> list[dict[str, Any]]:
    needle = name.strip().casefold()
    state = state.strip().upper()
    if not needle:
        return []

    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for item in registrations():
        if state and str(item.get("state", "")).upper() != state:
            continue
        entity = str(item.get("entity_name", ""))
        folded = entity.casefold()
        if folded == needle:
            exact.append(item)
        elif needle in folded:
            partial.append(item)

    combined = exact + partial
    combined.sort(
        key=lambda item: str(item.get("registration_date", "")), reverse=True
    )
    return combined[:50]


def stats() -> dict[str, Any]:
    rows = registrations()
    by_state = Counter(str(row.get("state", "")).upper() for row in rows)
    by_day = Counter(str(row.get("registration_date", "")) for row in rows)
    valid_dates = [
        parsed
        for parsed in (
            parse_registration_date(row.get("registration_date")) for row in rows
        )
        if parsed is not None
    ]
    return {
        "registrations": len(rows),
        "counts_by_state": dict(sorted((k, v) for k, v in by_state.items() if k)),
        "counts_by_day": dict(
            sorted(((k, v) for k, v in by_day.items() if k), reverse=True)
        ),
        "coverage": {
            "earliest_registration_date": min(valid_dates).isoformat()
            if valid_dates
            else None,
            "latest_registration_date": max(valid_dates).isoformat()
            if valid_dates
            else None,
            "note": (
                "Coverage is limited to states and dates present in the published "
                "source-backed seed dataset. Data is refreshed weekly."
            ),
        },
    }


def payment_accept(resource: str, description: str) -> dict[str, Any]:
    return {
        "scheme": "exact",
        "network": NETWORK,
        "maxAmountRequired": str(PRICE_ATOMIC),
        "resource": resource,
        "description": description,
        "mimeType": "application/json",
        "payTo": RECEIVING_ADDRESS,
        "maxTimeoutSeconds": 300,
        "asset": USDC_ADDRESS,
        "extra": {"name": "USD Coin", "version": "2"},
    }


def b64_json(value: Any) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def payment_terms(resource: str, description: str) -> tuple[dict[str, Any], dict[str, Any]]:
    accept = payment_accept(resource, description)
    v2 = {"x402Version": 2, "error": "Payment required", "accepts": [accept]}
    v1 = {"x402Version": 1, "error": "Payment required", "accepts": [accept]}
    return v2, v1


def blockscout_get(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": MODULE},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_chain_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, timezone.utc)

    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return parse_chain_timestamp(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def transaction_sender(transaction: dict[str, Any]) -> str:
    sender = transaction.get("from")
    if isinstance(sender, dict):
        return str(sender.get("hash") or sender.get("address") or "")
    return str(sender or transaction.get("from_address") or "")


def transfer_address(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("hash") or value.get("address") or "").lower()
    return str(value or "").lower()


def transfer_token_address(transfer: dict[str, Any]) -> str:
    token = transfer.get("token")
    if isinstance(token, dict):
        return str(token.get("address_hash") or token.get("address") or "").lower()
    return str(
        transfer.get("token_address")
        or transfer.get("token_contract_address")
        or token
        or ""
    ).lower()


def transfer_value(transfer: dict[str, Any]) -> int:
    value = transfer.get("total")
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        value = transfer.get("value")
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def is_redeemed(tx_hash: str) -> bool:
    redeemed = load_json(REDEEMED_FILE, {})
    if isinstance(redeemed, list):
        return tx_hash.lower() in {str(item).lower() for item in redeemed}
    if isinstance(redeemed, dict):
        return tx_hash.lower() in {str(key).lower() for key in redeemed}
    return False


def verify_bizreg_payment(tx_hash: str) -> tuple[bool, dict[str, Any]]:
    tx_hash = tx_hash.strip().lower()
    if not re.fullmatch(r"0x[a-f0-9]{64}", tx_hash):
        return False, {"error": "invalid_tx_hash"}
    if not SALES_ENABLED:
        return False, {"error": "sales_disabled"}
    if is_redeemed(tx_hash):
        return False, {"error": "transaction_already_redeemed"}

    transaction_url = f"https://base.blockscout.com/api/v2/transactions/{tx_hash}"
    try:
        transaction = blockscout_get(transaction_url)
    except urllib.error.HTTPError as exc:
        return False, {"error": "transaction_lookup_failed", "status": exc.code}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False, {"error": "transaction_lookup_failed"}

    if not isinstance(transaction, dict):
        return False, {"error": "invalid_blockscout_response"}
    if str(transaction.get("status", "")).lower() != "ok":
        return False, {"error": "transaction_not_successful"}

    timestamp = parse_chain_timestamp(
        transaction.get("timestamp")
        or transaction.get("block_timestamp")
        or transaction.get("confirmed_at")
    )
    if timestamp is None:
        return False, {"error": "transaction_timestamp_missing"}
    age = utc_now() - timestamp
    if age < timedelta(minutes=-5) or age > MAX_PAYMENT_AGE:
        return False, {"error": "transaction_outside_30_day_window"}

    transfers = transaction.get("token_transfers")
    if not isinstance(transfers, list):
        try:
            transfer_response = blockscout_get(
                transaction_url + "/token-transfers?type=ERC-20"
            )
            if isinstance(transfer_response, dict):
                transfers = transfer_response.get("items", [])
            elif isinstance(transfer_response, list):
                transfers = transfer_response
            else:
                transfers = []
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            OSError,
        ):
            transfers = []

    receiving = RECEIVING_ADDRESS.lower()
    qualifying_value = 0
    transfer_sender = ""
    for transfer in transfers:
        if not isinstance(transfer, dict):
            continue
        if transfer_token_address(transfer) != USDC_ADDRESS:
            continue
        destination = transfer_address(
            transfer.get("to") or transfer.get("to_address")
        )
        value = transfer_value(transfer)
        if destination == receiving and value >= PRICE_ATOMIC:
            qualifying_value = value
            transfer_sender = transfer_address(
                transfer.get("from") or transfer.get("from_address")
            )
            break

    if qualifying_value < PRICE_ATOMIC:
        return False, {"error": "qualifying_usdc_transfer_not_found"}

    return True, {
        "tx_hash": tx_hash,
        "sender": transfer_sender or transaction_sender(transaction),
        "value": qualifying_value,
        "confirmed_at": timestamp.isoformat().replace("+00:00", "Z"),
    }


def redeem_transaction(tx_hash: str, detail: dict[str, Any]) -> bool:
    tx_hash = tx_hash.lower()
    redeemed = load_json(REDEEMED_FILE, {})
    if not isinstance(redeemed, dict):
        redeemed = {}
    if tx_hash in {str(key).lower() for key in redeemed}:
        return False

    redeemed[tx_hash] = {
        "redeemed_at": iso_now(),
        "sender": detail.get("sender", ""),
        "value": detail.get("value", PRICE_ATOMIC),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = REDEEMED_FILE.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(redeemed, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, REDEEMED_FILE)
    return True


def log_sale(
    kind: str, params: dict[str, Any], tx_hash: str, sender: str
) -> None:
    record = {
        "ts": iso_now(),
        "kind": kind,
        "params": params,
        "tx_hash": tx_hash,
        "sender": sender,
        "amount_usd": PRICE_USD,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with SALES_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def within_rate_limit(ip: str) -> bool:
    now = time.monotonic()
    with RATE_LOCK:
        bucket = REQUESTS_BY_IP[ip]
        while bucket and now - bucket[0] >= RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            return False
        bucket.append(now)
        return True


LANDING_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>BizRegSignal</title></head>
<body>
<h1>BizRegSignal</h1>
<p>Tiger Operations API for source-backed US new-business registration signals.</p>
<ul>
<li><code>GET /health</code> — free service status.</li>
<li><code>GET /bizreg/stats</code> — free coverage statistics.</li>
<li><code>GET /bizreg/new?state=VA&amp;days=7&amp;category=&amp;limit=20</code> — $0.05 USDC.</li>
<li><code>GET /bizreg/company?company=Example&amp;state=VA</code> — $0.05 USDC.</li>
<li><code>POST /fulfill-bizreg</code> — redeem a verified Base USDC payment.</li>
</ul>
<p>Network: eip155:8453. Asset: Base USDC. Payment protocol: x402 v1 and v2.</p>
<p><a href="/catalog">Catalog</a> · <a href="/openapi.json">OpenAPI</a> · <a href="/llms.txt">llms.txt</a></p>
</body>
</html>
"""


def catalog_document() -> dict[str, Any]:
    return {
        "name": "BizRegSignal",
        "provider": "Tiger Operations",
        "module": MODULE,
        "network": NETWORK,
        "currency": "USDC",
        "resources": [
            {
                "method": "GET",
                "path": "/bizreg/new",
                "price_usd": PRICE_USD,
                "fulfill_kind": "new",
            },
            {
                "method": "GET",
                "path": "/bizreg/company",
                "price_usd": PRICE_USD,
                "fulfill_kind": "company",
            },
        ],
        "fulfillment": {"method": "POST", "path": "/fulfill-bizreg"},
    }


def x402_document() -> dict[str, Any]:
    return {
        "x402Version": 2,
        "provider": "Tiger Operations",
        "sales_enabled": SALES_ENABLED,
        "resources": [
            {
                "method": "GET",
                "path": "/bizreg/new",
                "price": "$0.05",
                "accepts": [
                    payment_accept(
                        "/bizreg/new",
                        "Recent US business registrations matching query filters",
                    )
                ],
            },
            {
                "method": "GET",
                "path": "/bizreg/company",
                "price": "$0.05",
                "accepts": [
                    payment_accept(
                        "/bizreg/company",
                        "Business-registration records matching a company name",
                    )
                ],
            },
        ],
    }


def openapi_document() -> dict[str, Any]:
    registration_schema = {
        "type": "object",
        "required": [
            "entity_name",
            "state",
            "entity_type",
            "registration_date",
            "category",
            "city",
            "source_url",
            "scraped_at",
        ],
        "properties": {
            "entity_name": {"type": "string"},
            "state": {"type": "string"},
            "entity_type": {"type": "string"},
            "registration_date": {"type": "string", "format": "date"},
            "category": {"type": "string"},
            "city": {"type": "string"},
            "source_url": {"type": "string", "format": "uri"},
            "scraped_at": {"type": "string"},
        },
    }
    payment_response = {
        "description": "x402 payment required",
        "headers": {
            "PAYMENT-REQUIRED": {
                "description": "Base64-encoded x402 v2 terms",
                "schema": {"type": "string"},
            },
            "X-PAYMENT-REQUIRED": {
                "description": "Base64-encoded x402 v1 terms",
                "schema": {"type": "string"},
            },
        },
    }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "BizRegSignal",
            "version": "1.0.0",
            "description": (
                "Tiger Operations source-backed US business-registration signal API. "
                "Paid resources cost $0.05 USDC on Base."
            ),
        },
        "servers": [{"url": "/"}],
        "paths": {
            "/health": {
                "get": {
                    "summary": "Service health",
                    "responses": {"200": {"description": "Healthy"}},
                }
            },
            "/bizreg/stats": {
                "get": {
                    "summary": "Dataset counts and coverage",
                    "responses": {"200": {"description": "Statistics"}},
                }
            },
            "/bizreg/new": {
                "get": {
                    "summary": "Request recent registration signals",
                    "parameters": [
                        {
                            "name": "state",
                            "in": "query",
                            "schema": {"type": "string", "example": "VA"},
                        },
                        {
                            "name": "days",
                            "in": "query",
                            "schema": {
                                "type": "integer",
                                "default": 7,
                                "minimum": 0,
                                "maximum": 365,
                            },
                        },
                        {
                            "name": "category",
                            "in": "query",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {
                                "type": "integer",
                                "default": 20,
                                "minimum": 1,
                                "maximum": 100,
                            },
                        },
                    ],
                    "responses": {
                        "402": payment_response,
                        "200": {
                            "description": "Available through fulfillment after payment",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": registration_schema,
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/bizreg/company": {
                "get": {
                    "summary": "Request company registration matches",
                    "parameters": [
                        {
                            "name": "company",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "state",
                            "in": "query",
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"402": payment_response},
                }
            },
            "/fulfill-bizreg": {
                "post": {
                    "summary": "Verify payment and return purchased data",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["tx_hash", "kind", "params"],
                                    "properties": {
                                        "tx_hash": {"type": "string"},
                                        "kind": {
                                            "type": "string",
                                            "enum": ["new", "company"],
                                        },
                                        "params": {"type": "object"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Verified paid result"},
                        "400": {"description": "Invalid request or payment"},
                        "409": {"description": "Payment already redeemed"},
                    },
                }
            },
        },
        "components": {"schemas": {"Registration": registration_schema}},
    }


LLMS_TEXT = """# BizRegSignal

Provider: Tiger Operations
Module: bizreg-signal/1.0
Purpose: source-backed recent US business-registration signals for AI agents.
Price: $0.05 USDC per paid call.
Network: Base (eip155:8453).
Payment protocol: x402 v1 and v2.

Free:
GET /health
GET /bizreg/stats
GET /catalog
GET /.well-known/x402
GET /openapi.json

Paid:
GET /bizreg/new?state=VA&days=7&category=&limit=20
GET /bizreg/company?company=Example&state=VA

An unpaid paid-resource request returns HTTP 402 and payment terms. After sending
the required Base USDC transfer, POST /fulfill-bizreg with:
{"tx_hash":"0x...","kind":"new","params":{"state":"VA","days":7,"category":"","limit":20}}

Each transaction hash can be redeemed once.
"""


class Handler(BaseHTTPRequestHandler):
    server_version = MODULE
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        print(
            json.dumps(
                {
                    "ts": iso_now(),
                    "ip": self.client_address[0],
                    "message": fmt % args,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(
        self,
        status: int,
        body: Any,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_bytes(status, encoded, "application/json; charset=utf-8", extra_headers)

    def send_error_json(self, status: int, error: str, **details: Any) -> None:
        self.send_json(status, {"error": error, **details})

    def rate_limit_or_continue(self) -> bool:
        if within_rate_limit(self.client_address[0]):
            return True
        self.send_json(
            429,
            {"error": "rate_limit_exceeded", "limit": RATE_LIMIT, "window_seconds": 60},
            {"Retry-After": "60"},
        )
        return False

    def parsed_url(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlsplit(self.path)
        return parsed.path, urllib.parse.parse_qs(
            parsed.query, keep_blank_values=True
        )

    @staticmethod
    def first(query: dict[str, list[str]], key: str, default: str = "") -> str:
        values = query.get(key)
        return values[0] if values else default

    @staticmethod
    def bounded_int(
        raw: Any, default: int, minimum: int, maximum: int
    ) -> int:
        try:
            return max(minimum, min(int(raw), maximum))
        except (TypeError, ValueError):
            return default

    def send_payment_required(
        self,
        resource: str,
        description: str,
        preview: dict[str, Any],
    ) -> None:
        v2, v1 = payment_terms(resource, description)
        body = {
            "x402": v2,
            "preview": preview,
            "how_to_pay": [
                "Select an entry from x402.accepts.",
                "Transfer at least 50000 atomic units ($0.05) of Base USDC to payTo.",
                "POST the confirmed tx_hash, resource kind, and original params to /fulfill-bizreg.",
            ],
        }
        self.send_json(
            402,
            body,
            {
                "PAYMENT-REQUIRED": b64_json(v2),
                "X-PAYMENT-REQUIRED": b64_json(v1),
            },
        )

    def do_OPTIONS(self) -> None:
        self.send_bytes(204, b"", "text/plain")

    def do_GET(self) -> None:
        if not self.rate_limit_or_continue():
            return

        path, query = self.parsed_url()

        if path == "/":
            self.send_bytes(
                200, LANDING_HTML.encode("utf-8"), "text/html; charset=utf-8"
            )
            return

        if path == "/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "sales_enabled": SALES_ENABLED,
                    "registrations": len(registrations()),
                },
            )
            return

        if path == "/bizreg/stats":
            self.send_json(200, stats())
            return

        if path == "/catalog":
            self.send_json(200, catalog_document())
            return

        if path == "/.well-known/x402":
            self.send_json(200, x402_document())
            return

        if path == "/openapi.json":
            self.send_json(200, openapi_document())
            return

        if path == "/llms.txt":
            self.send_bytes(
                200, LLMS_TEXT.encode("utf-8"), "text/plain; charset=utf-8"
            )
            return

        if path == "/bizreg/new":
            state = self.first(query, "state").strip().upper()
            days = self.bounded_int(self.first(query, "days", "7"), 7, 0, 365)
            category = self.first(query, "category").strip()
            limit = self.bounded_int(self.first(query, "limit", "20"), 20, 1, 100)
            resource = (
                "/bizreg/new?"
                + urllib.parse.urlencode(
                    {
                        "state": state,
                        "days": days,
                        "category": category,
                        "limit": limit,
                    }
                )
            )
            self.send_payment_required(
                resource,
                "Recent US business registrations matching query filters",
                {
                    "kind": "new",
                    "params": {
                        "state": state,
                        "days": days,
                        "category": category,
                        "limit": limit,
                    },
                    "available_matches": len(
                        find_registrations(state, days, category, limit)
                    ),
                    "price_usd": PRICE_USD,
                },
            )
            return

        if path == "/bizreg/company":
            company = self.first(query, "company").strip()
            state = self.first(query, "state").strip().upper()
            if not company:
                self.send_error_json(400, "company_required")
                return
            resource = (
                "/bizreg/company?"
                + urllib.parse.urlencode({"company": company, "state": state})
            )
            self.send_payment_required(
                resource,
                "Business-registration records matching a company name",
                {
                    "kind": "company",
                    "params": {"company": company, "state": state},
                    "matches_available": bool(find_company(company, state)),
                    "price_usd": PRICE_USD,
                },
            )
            return

        self.send_error_json(404, "not_found", path=escape(path))

    def do_POST(self) -> None:
        if not self.rate_limit_or_continue():
            return

        path, _ = self.parsed_url()
        if path != "/fulfill-bizreg":
            self.send_error_json(404, "not_found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error_json(400, "invalid_content_length")
            return
        if length <= 0 or length > 64 * 1024:
            self.send_error_json(400, "invalid_body_size")
            return

        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error_json(400, "invalid_json")
            return
        if not isinstance(body, dict):
            self.send_error_json(400, "json_object_required")
            return

        tx_hash = str(body.get("tx_hash", "")).strip().lower()
        kind = str(body.get("kind", "")).strip().lower()
        params = body.get("params", {})
        if not isinstance(params, dict):
            self.send_error_json(400, "params_object_required")
            return
        if kind not in {"new", "company"}:
            self.send_error_json(400, "invalid_kind", allowed=["new", "company"])
            return

        if kind == "new":
            state = str(params.get("state", "")).strip().upper()
            days = self.bounded_int(params.get("days", 7), 7, 0, 365)
            category = str(params.get("category", "")).strip()
            limit = self.bounded_int(params.get("limit", 20), 20, 1, 100)
            clean_params = {
                "state": state,
                "days": days,
                "category": category,
                "limit": limit,
            }
            result = find_registrations(state, days, category, limit)
        else:
            company = str(params.get("company", "")).strip()
            state = str(params.get("state", "")).strip().upper()
            if not company:
                self.send_error_json(400, "company_required")
                return
            clean_params = {"company": company, "state": state}
            result = find_company(company, state)

        with FILE_LOCK:
            ok, detail = verify_bizreg_payment(tx_hash)
            if not ok:
                status = (
                    409
                    if detail.get("error") == "transaction_already_redeemed"
                    else 400
                )
                self.send_json(status, {"ok": False, **detail})
                return
            if not redeem_transaction(tx_hash, detail):
                self.send_error_json(409, "transaction_already_redeemed")
                return
            log_sale(
                kind,
                clean_params,
                tx_hash,
                str(detail.get("sender", "")),
            )

        self.send_json(
            200,
            {
                "ok": True,
                "kind": kind,
                "params": clean_params,
                "count": len(result),
                "data": result,
                "payment": {
                    "tx_hash": tx_hash,
                    "sender": detail.get("sender", ""),
                    "amount_usd": PRICE_USD,
                    "network": NETWORK,
                },
            },
        )


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(
        json.dumps(
            {
                "ts": iso_now(),
                "module": MODULE,
                "host": HOST,
                "port": PORT,
                "sales_enabled": SALES_ENABLED,
                "registrations": len(registrations()),
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
