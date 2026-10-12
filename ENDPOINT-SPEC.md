# VENTURE 2 — BizRegSignal — endpoint spec + seed-data sourcing plan

Brand: **BizRegSignal — Tiger Operations** (Agent Data Supply Company). Never "Conor O'Heir" on anything public.
Product: per-call feed of newly registered US businesses. Buyer: sales/prospecting agents that want first-mover outreach to brand-new companies.
Price: $0.05 USDC per call on Base (eip155:8453), x402 v1+v2, same payment rail as FreshSignal. Zero marginal cost (stdlib-only server, static JSON seed DB, free upstreams).

## Endpoints

| Method | Path | Price | Behavior |
|---|---|---|---|
| GET | /health | free | `{ok, sales_enabled, registrations}` |
| GET | /bizreg/stats | free | counts by state/day + coverage note (discovery hook for buyers) |
| GET | /bizreg/new?state=VA&days=7&category=&limit=20 | 402 → $0.05 | registrations in state within trailing `days` (max 30), optional `category` filter; 402 preview shows count + sample; POST /fulfill-bizreg with `tx_hash` returns the data |
| GET | /bizreg/company?company=<name>&state= | 402 → $0.05 | registration record for one company |
| POST | /fulfill-bizreg | n/a | `{tx_hash, kind}` — verifies on-chain USDC transfer (Blockscout free API), returns data, records redemption |

## Record schema
`{entity_name, state, entity_type (LLC|Corporation|Other), registration_date (YYYY-MM-DD), category, city, source_url (state SOS record), scraped_at}`

## Payment (copy FreshSignal exactly)
- `extra` advertises EIP-712 domain name "USD Coin" / version "2" (the bug that blocked Scout Packs payments).
- Receiving address: env `BIZREGSIGNAL_RECEIVING_ADDRESS`, default 0xAF7B70D8487EE6193701597E67f56A5902d23913.
- `data/redeemed.json` replay-guard; `data/bizreg_sales.jsonl` sales ledger.

## Kill rule
14 days from live listing → ≥5 paid calls from ≥3 distinct wallets = GO; else KILL + postmortem. (Same as V1.)

## Seed-data sourcing plan ($0, free upstreams only, verified-records-only)
1. **State open-data portals (Socrata/data.gov)**: several states publish machine-readable business-entity datasets updated weekly — e.g., Virginia SCC, Texas Comptroller, New York DOS. Pull the weekly snapshot, diff against the previous week's file, keep only NEW entities. All public records.
2. **Florida Sunbiz**: public search; batch-verify the seed records' source URLs.
3. **OpenCorporates**: backfill/verify entity details (free tier).
4. Start coverage: VA + FL + TX (~150–250 verified recent registrations, each with a public SOS source URL — NO fabricated records, NO fake dates; if a record can't be verified it's excluded).
5. Refresh: Scout lane pulls weekly snapshots, diffs, appends; dedupe by (state + entity_name + registration_date).

## Why this can sell
- Adjacent to the proven enrichment buyer (same wallets, same per-call price band $0.005–$0.75).
- New-company outreach is a classic sales-agent wedge (first-mover advantage); no maintained per-call agent-payable feed exists (empty shelf verified 2026-10-02 hunt #2, re-confirmed 2026-10-11).
- Per-call $0.05 fits x402 wallet policies; fresh data is perishable so the feed has repeat-purchase mechanics.

## Build assets (this dir)
- `server.py` — stdlib-only, mirrors ~/workspace/fresh-signal/server.py architecture
- `data/registrations.json` — seed DB (populated by Scout verification pass, not by code)
- `Dockerfile`, `run.sh` — same as FreshSignal
- `README.md`, `SKILL.md`, `sales-assets.md` (402index variant C payload, dev.to variant A outline)
- `DEPLOY-SPEC.md` — browser deploy spec (same Railway path as FreshSignal; Railway trial-budget note applies)
