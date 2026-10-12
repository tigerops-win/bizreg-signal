# BizRegSignal

BizRegSignal is a Tiger Operations API for AI agents that need recent US new-business registration signals. It exposes source-backed registration records by state, date, category, and company name.

Paid calls cost **$0.05 USDC** on Base (`eip155:8453`). The amount is intentionally small enough to fit typical autonomous-agent wallet policies. Payment uses an x402-compatible v1 and v2 flow.

## Endpoints

| Method | Endpoint | Price | Purpose |
|---|---|---:|---|
| `GET` | `/` | Free | Short machine-oriented landing page |
| `GET` | `/health` | Free | Health, sales status, and loaded record count |
| `GET` | `/bizreg/stats` | Free | Counts by state and day plus coverage notes |
| `GET` | `/bizreg/new?state=VA&days=7&category=&limit=20` | $0.05 | Recent registrations matching filters |
| `GET` | `/bizreg/company?company=<name>&state=` | $0.05 | Registration records matching a company |
| `POST` | `/fulfill-bizreg` | Paid transaction | Verify and redeem payment, then return data |
| `GET` | `/catalog` | Free | Machine-readable product catalog |
| `GET` | `/.well-known/x402` | Free | x402 resources and payment terms |
| `GET` | `/llms.txt` | Free | Compact agent instructions |
| `GET` | `/openapi.json` | Free | OpenAPI 3.0 description |

## x402 payment flow

First request a paid resource without payment:

```sh
curl -i \
  "https://YOUR-DOMAIN/bizreg/new?state=VA&days=7&category=&limit=20"
```

The API returns HTTP `402` with:

- An `x402` JSON object containing v2 terms.
- `PAYMENT-REQUIRED`, containing base64-encoded v2 terms.
- `X-PAYMENT-REQUIRED`, containing base64-encoded v1 terms.
- A preview and fulfillment instructions.

Pay at least `50000` atomic units of Base USDC — equal to `$0.05` with six decimals — to the `payTo` address in the selected acceptance object. The USDC contract is:

```text
0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
```

After the transaction confirms, redeem it once:

```sh
curl -sS \
  -X POST \
  -H "Content-Type: application/json" \
  --data '{
    "tx_hash": "0xYOUR_CONFIRMED_TRANSACTION_HASH",
    "kind": "new",
    "params": {
      "state": "VA",
      "days": 7,
      "category": "",
      "limit": 20
    }
  }' \
  "https://YOUR-DOMAIN/fulfill-bizreg"
```

Company lookup uses the same flow:

```sh
curl -sS \
  -X POST \
  -H "Content-Type: application/json" \
  --data '{
    "tx_hash": "0xYOUR_CONFIRMED_TRANSACTION_HASH",
    "kind": "company",
    "params": {
      "company": "Example",
      "state": "VA"
    }
  }' \
  "https://YOUR-DOMAIN/fulfill-bizreg"
```

A transaction must be successful, no more than 30 days old, and contain a qualifying Base USDC transfer to the configured receiving address. Each transaction hash can be redeemed only once.

## Data

The service reads `data/registrations.json`, which must be a JSON list whose records contain:

```json
{
  "entity_name": "Example LLC",
  "state": "VA",
  "entity_type": "Limited Liability Company",
  "registration_date": "2026-10-01",
  "category": "Professional Services",
  "city": "Richmond",
  "source_url": "https://official-state-source.example/record",
  "scraped_at": "2026-10-02T12:00:00Z"
}
```

Seed coverage is limited to the states and dates represented in that file. Use `/bizreg/stats` to inspect actual coverage. The dataset is refreshed weekly from its documented public sources.

## Data integrity: no fake data

BizRegSignal does not invent businesses, registration dates, locations, categories, or source links. Every published record must be traceable to the `source_url` stored with it. If a field cannot be supported by the source, it must remain empty or the record must be excluded. Synthetic demonstration records must never be mixed into production results.

## Local run

```sh
chmod +x run.sh
BIZREGSIGNAL_RECEIVING_ADDRESS=0xAF7B70D8487EE6193701597E67f56A5902d23913 ./run.sh
```

`SALES_ENABLED` remains false unless `BIZREGSIGNAL_RECEIVING_ADDRESS` is a syntactically valid `0x` address containing 40 hexadecimal characters.

## Deployment

The repository includes a Dockerfile suitable for Railway:

```sh
docker build -t bizreg-signal .
docker run --rm -p 8080:8080 \
  -e BIZREGSIGNAL_RECEIVING_ADDRESS=0xAF7B70D8487EE6193701597E67f56A5902d23913 \
  bizreg-signal
```

The service binds to `0.0.0.0` and uses Railway's `PORT` environment variable.

Production deployments need persistent storage for:

- `data/redeemed.json`, which prevents transaction replay.
- `data/bizreg_sales.jsonl`, which records fulfilled sales.

Without persistent storage, redeployments can erase replay state. Attach a Railway volume at `/app/data` or move these ledgers to a durable external store before meaningful production traffic.

## Kill rule

Evaluate the product after 14 days:

- **GO:** at least 5 paid calls from at least 3 distinct paying wallets.
- **KILL:** anything below either threshold.

Do not reinterpret previews, test calls, unpaid requests, or internal wallet activity as customer demand.
