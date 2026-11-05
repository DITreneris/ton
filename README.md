# TON GRAM paper worker

Paper-mode only worker for **native GRAM** (formerly Toncoin) ↔ **issuer-verified USD₮** on **TON mainnet**, using **one STON.fi v2 route**.

This build **does not sign, does not hold private keys, and does not broadcast**. It reads live mainnet quotes and pool data, runs a deterministic baseline, and writes a simulated ledger plus the monitoring snapshot the Grok Risk Monitor expects.

## What is pinned

Identity is addresses, never tickers.

| Item | Pinned value | Source |
| --- | --- | --- |
| Network | `mainnet` | Fail-closed if anything else is observed |
| USD₮ Jetton master | `EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs` | [Tether supported protocols](https://tether.to/en/supported-protocols/) |
| Native GRAM | `EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c` | TON zero address / STON.fi `kind=Ton` |
| STON.fi v2 pool | `EQCGScrZe1xbyWqWDvdI6mzP-GAcAWFv6ZXuaJOuSqemxku4` | Highest-liquidity v2 GRAM/USD₮ pool |
| STON.fi v2 router | `EQCS4UEa5UaJLzOyyKieqQOQ2P9M-7kXpkO5HnP3Bv250cN3` | ConstantProduct v2.2 |
| pTON master | `EQBnGWMCf3-FZZq1W4IWcWiGAc3PHuZ0_H-7sad2oY00o83S` | Router metadata, pTON 2.1 |
| pTON wallet | `EQCSIMGBps_qzRG3uPYhON8bucyCtu0mYdL1-u4gSz77IBa3` | Wallet of that router |

Scope verification fetches STON.fi asset, pool, and router metadata, then both swap simulations with `pool_address` pinned. Any ticker look-alike, wrong network, wrong master, or a quote that lands on another router/pool **fails closed**. A failed scope latches a sticky entry halt.

## Limits (defaults)

All amounts are integer base units: GRAM nano (9 dp), USD₮ micro (6 dp).

| Control | Default |
| --- | --- |
| Position cap | 2% of funding cap (`POSITION_CAP_BPS=200`) |
| Funding cap | 10,000 USD₮ |
| Max aggregate exposure | Same 2% cap, including reserved intents |
| Daily loss | 1% of funding cap |
| Drawdown | 3% from peak marked equity |
| Max trade | 50 USD₮ |
| Max quote age | 15 s |
| Min pool liquidity | $500,000 |
| GRAM gas reserve | 0.25 GRAM (cannot be offered) |
| Conservative extra slippage | 50 bps |
| Delay stress | 8 s + 5 bps adverse |
| Network cost | quoted gas + 1× extra |
| One unresolved intent | Per paper wallet |
| Entry halt | Sticky across restarts; requires `ton-gram rearm` or `POST /v1/control/rearm` |

Paper fills apply pool fee, price impact, stressed gas, conservative slippage, and delay. Restart recovery reconciles reserved intents to a terminal paper state **before** new intents are accepted.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # set TON_GRAM_SNAPSHOT_BEARER_TOKEN
mkdir -p data
python -m ton_gram worker
```

The process starts the poller and a read-only HTTP server on **port 43177** (`TON_GRAM_PORT`, or Railway's `PORT` if that is unset).

```bash
# public health / dashboard (no secrets)
curl -s http://127.0.0.1:43177/healthz
open http://127.0.0.1:43177/

# Grok monitor snapshot (bearer required)
curl -s -H "Authorization: Bearer $TON_GRAM_SNAPSHOT_BEARER_TOKEN" \
  http://127.0.0.1:43177/v1/snapshot
```

Other commands:

```bash
python -m ton_gram once --snapshot-out artifacts/sample_snapshot.json
python -m ton_gram verify-scope
python -m ton_gram report
python -m ton_gram halt
python -m ton_gram rearm
python -m ton_gram validate-result path/to/MONITOR_RESULT.json --snapshot-json artifacts/sample_snapshot.json
pytest
```

## Snapshot endpoint shape

`GET /v1/snapshot` returns one JSON object:

```json
{
  "schema_version": "1.0",
  "snapshot_id": "snap_...",
  "as_of_utc": "2026-11-05T04:40:00Z",
  "policy_version": "paper-1.0.0",
  "mode": "PAPER",
  "scope_verification": { "status": "PASS", "network": "mainnet", "checks": [] },
  "freshness": { "quote_age_ms": 12, "max_quote_age_ms": 15000, "stale": false },
  "feed_health": { "stonfi_quotes": { "status": "UP", "latency_ms": 80 } },
  "required_coverage": { "quotes_both_directions": true, "pool_liquidity": true, "scope": true },
  "incidents": [],
  "open_execution_state": { "unresolved_intents": [], "entry_halt": false, "position": {} },
  "evidence_registry": [{ "evidence_id": "ev_...", "kind": "QUOTE" }]
}
```

`POST /v1/monitor-result` validates the Grok `MONITOR_RESULT` contract (schema, snapshot binding, evidence-ID membership, late-result window) and records `VALID` / `INVALID_*` / `UNAVAILABLE`. Invalid or missing assessments have **zero execution authority**.

## Telegram

Outbound only, **off by default**. Set `TON_GRAM_TELEGRAM_ENABLED=true`, `TON_GRAM_TELEGRAM_BOT_TOKEN`, and `TON_GRAM_TELEGRAM_CHAT_ID`. Alerts are deduplicated per key for `TON_GRAM_TELEGRAM_DEDUPE_S` seconds (default 1 hour). There is no inbound Telegram command surface.

## 24/7 on a small VPS / Fly / Railway

Docker image is self-contained. The Dockerfile does **not** declare `VOLUME` (Railway rejects that instruction). Persist `/data` yourself so the SQLite ledger, halt latch, and snapshots survive restarts: use `docker run -v` locally, or attach a Railway volume at `/data`.

```bash
docker build -t ton-gram-paper .
docker run -d --name ton-gram-paper --restart unless-stopped \
  -p 43177:43177 \
  -v gram-paper:/data \
  -e TON_GRAM_SNAPSHOT_BEARER_TOKEN=long-random-token \
  -e TON_GRAM_DATA_DIR=/data \
  ton-gram-paper
```

On a VPS, put Caddy or nginx in front and require the bearer token on `/v1/*`. Leave `/healthz` open for the platform health check.

**Fly.io**

```bash
fly launch --no-deploy
fly secrets set TON_GRAM_SNAPSHOT_BEARER_TOKEN=long-random-token
fly volumes create gram_paper --size 1
fly deploy
```

Use the included `fly.toml` (`internal_port = 43177`, volume mounted at `/data`).

**Railway**

Create a service from this repo, set the same env vars, and attach a **Railway volume** at `/data` (do not rely on a Dockerfile `VOLUME`). The worker listens on Railway's `PORT` when `TON_GRAM_PORT` is unset. Health check: `GET /healthz`.

Backup `data/paper.sqlite3` regularly. This is the only durable store.

## Baseline strategy

Deterministic mid-deviation fade:

1. Mid = average implied USD₮-micro per GRAM from both pinned quotes.
2. After `STRATEGY_LOOKBACK` samples, compare mid to SMA.
3. If `|deviation| >= STRATEGY_ENTRY_DEVIATION_BPS`, emit a timestamped candidate fading the move.
4. Risk must still approve it. Out-of-policy size, stale quotes, halt, or failed scope produce **zero intents**.

## Hard constraints

- No wallet mnemonic, no private key, no signer, no `sendBoc`.
- Recipients, refund addresses, and opcodes are declared for a later live pilot and are **not encoded** here.
- A poisoned snapshot or malformed monitor output cannot authorize anything. The worker keeps reconciling paper intents on its own clock.
