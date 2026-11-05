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
| Delay stress | measured p90 latency + 2 s (fallback 8 s + 5 bps if no profile) |
| Network cost | measured p90 net GRAM + 25% (fallback quoted gas + 1× extra) |
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
python -m ton_gram report --day 2026-11-05
python -m ton_gram learn --day 2026-11-05
python -m ton_gram docs sync
python -m ton_gram docs show paper-policy
python -m ton_gram measure-gas --target 60
python -m ton_gram drill all --isolated --http
python -m ton_gram telegram-test
python -m ton_gram halt
python -m ton_gram rearm
python -m ton_gram reset-paper
python -m ton_gram validate-result path/to/MONITOR_RESULT.json --snapshot-json artifacts/sample_snapshot.json
pytest
```

## Snapshot endpoint shape

`GET /v1/snapshot` returns one JSON object. `GET /v1/report/daily` returns the UTC paper day. `GET /v1/docs` returns the document index.

## Closed learning loop

The Prompt Anatomy diagram is a full coding OS. This repo is a paper trader. Only the right-hand loop is wired here: **logs → evaluate → lessons → advisory rules**.

`python -m ton_gram learn` scores the UTC paper day. Rules are advisory. No auto-apply. It will not lower the 25 bps entry gate or turn signing on.

## Document index and versions

Operator documents live in `docs/`. The machine index is `docs/registry.json`. The human index is `docs/INDEX.md`. Each publish writes `docs/versions/<id>/vN.md` and appends a line to `CHANGELOG.md`.

```bash
python -m ton_gram docs sync
python -m ton_gram docs show paper-policy --version 1
curl -s -H "Authorization: Bearer $TOKEN" "$HOST/v1/docs"
```

Unchanged hashes are no-ops. There is no CMS, no extra database, and no auto-edit of paper policy from the learning loop.

See `CHANGELOG.md` for dated history.
