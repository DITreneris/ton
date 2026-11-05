# ROADMAP — self-sustaining profitable TON GRAM bot

**Destination.** One unsupervised worker that trades **native GRAM ↔ issuer-verified USD₮** on the pinned STON.fi v2 route, pays its own gas and hosting from net edge, and stays up without daily babysitting.

**Clock.** Evidence opens the next gate. No phase waits on a calendar month. A green table on a Tuesday is a Tuesday go — not January 2027.

**How ambition is allowed to rise.** Goals get bigger. Controls do not get looser. This repo stays paper-only and keyless until Phase 3 is explicitly opened. Profit is net after pool fee, price impact, 50 bps slip, delay, and measured p90 gas + 25%. Win rate is not a goal.

## Now (5 November 2026)

The paper worker is up. First approved fade: `int_20261105T133435Z_1971` at 15:34 Europe/Vilnius (+25 bps). Same day `LIMIT_BREACH` on marked 5 000 GRAM inventory (~200 bps cheaper vs morning, ≈ −$118). Instrument works. **No proven net edge yet.**

Phase 0 and Phase 1 run **in parallel from today**. Do not sit out November waiting to start counting fills.

| Capability | In repo |
| --- | --- |
| Pinned identity, fail-closed scope, 25 bps fade, risk, stressed fills, recover, drills, Telegram, `learn` | Yes |
| Signing / broadcast / wallet | **Absent on purpose** |

## What “profitable” and “self-sustaining” mean

**Profitable.** Across the scored paper (then live) fills, net after stressed costs is **strictly positive**, and average net per approved fill exceeds average all-in cost of that fill. One lucky afternoon does not count — see Phase 1 exit.

**Self-sustaining.** Railway + Telegram ≤1 operator hour / week. Gas floor and hosting paid from **retained net**, not fresh deposits.

**Later, not now.** Second DEX, second pair, inbound Telegram commands, Grok sizing or signing.

## Non-negotiables (every phase)

- No mnemonic, no signer, no `sendBoc` in **this** repository until Phase 3 is explicitly opened.
- Do not lower `STRATEGY_ENTRY_DEVIATION_BPS` from 25 to manufacture fills.
- Do not treat a healthy bid/ask as a broken book (`MID_SPREAD_MAX_BPS=200`).
- Do not raise `max_all_in_cost_bps` (80) to manufacture approvals.
- Official USD₮ master and the pinned pool/router stay fail-closed.
- `learn` / Grok stay **advisory**. Kill switch is `POST /v1/control/halt` on the running host.
- One pair, one router.

## Phase 0 — Instrument holds

**Open:** now. **Exit the day all of these are true** (earliest: 7 UTC days from 5 Nov).

- Railway paper up. Rotate any bearer that was pasted into chat.
- Daily Telegram card + `POST /v1/learn` on the **Railway** host.
- One isolated `drill all` green on that host.
- **Exit.** 7 consecutive UTC days of ledger, feed uptime floor ≥99%, no unexplained `HARD_HALT`. Missing days reset the count. Flat book is fine.

No strategy rewrite here.

## Phase 1 — Edge verdict (open now)

**Open:** now, in parallel with Phase 0. Goal: does the **current** 25 bps fade pay after costs?

Score the live ledger. Do not wait for 1 December.

| Result | Meaning | Next |
| --- | --- | --- |
| ≥8 approved paper fills **and** net > 0 after stressed costs **and** those fills span ≥7 UTC days | Edge candidate | Phase 2 **the same day** |
| Candidates emit, all rejected by risk | Inspect `rejected_candidates_by_reason`. `all_in_cost` = fee+impact+50 > 80. `daily_loss_limit` + `LIMIT_BREACH` with unrealized dominating = marked 5 000 GRAM ($100 ≈ $0.02/GRAM ≈ 170 bps at $1.16); flat cycles do not latch. Do not lower 25 bps or raise the all-in cap | Stay paper, keep scoring |
| 0 candidates, almost all `skip=flat` | Book has no 25 bps excursion. That is data. | New **paper** signal behind the same risk, or no-go this fade |
| Spread or halt dominates | Instrumentation or feed problem | Fix worker, do not go live |

Still no keys.

## Phase 2 — Go / no-go (same day as a green Phase 1)

Not a month. Review the hour Phase 1 is green.

### Go (all must be true)

1. Phase 0 exit met (7 clean days).
2. Phase 1 table is “edge candidate”.
3. Isolated drills green on the production host in the last 7 days.
4. Restart recovery: kill Railway mid-intent. After `delay_stress_s`, exactly one fill. Past `intent_ttl_s` (45 s) expires with no fill and no `SETTLEMENT_UNKNOWN`.
5. Stale quote, scope mismatch, daily-loss, drawdown — reject or halt as designed.
6. Written live budget: first live clip ≤ **$10**, hard cap **$50**, 2% position cap, $10k paper-equivalent funding.
7. Isolated **execution gateway** designed as a **separate** service. This worker stays the brain. Keys live only in the gateway.

### No-go (any one)

- Phase 1 is not “edge candidate”.
- Unexplained `SETTLEMENT_UNKNOWN` or a halt that was rearmed without a written reason.
- Desire to go live by lowering 25 bps, widening size, or adding a second DEX.

**No-go action.** Stay paper. Iterate a new paper signal or retire this fade. Do not open Phase 3.

**Go action.** Freeze policy version. Open Phase 3 **that day** with a note in `CHANGELOG.md` and `docs/roadmap.md`.

## Phase 3 — Micro live (hours after go)

First real clip, still green after on-chain costs.

- Gateway signs **only** what this worker already approved. Bind quote id, pool, router, size, min-out. Unknown settlement **blocks retries**.
- This repo: read-only live-intent projection. Paper twin stays on. Same risk.
- First 10 live fills: max $10, then **stop and score**. Net ≤ 0 after real gas → back to Phase 1.
- One human approval for **gateway power-on**. No per-trade chat commands.

## Phase 4 — Self-sustain (after 10 live fills are net > 0)

- Raise toward $50 **only** from retained net.
- Top the 0.25 GRAM gas floor from earned GRAM.
- Re-run `measure-gas` if the profile is older than 30 days. p90 net cost move > 25% → freeze size.
- Self-sustaining = 30-day live net ≥ hosting + measured gas + 2× margin. That window cannot start before live fills exist.

## Phase 5 — Scale inside the same box (after self-sustain)

- Step +$25 only when the last 60 live days are net-positive **at the current size**. Cap remains 2% of funding.
- Edge fades → step down. `learn` recommends; operator applies.
- Second pair / second venue stays parked until this route pays.

**Done** looks like: self-sustaining live bot, one route, keys still outside this tree — **or** an honest paper retirement of the 25 bps fade. Burning money to look fast is not a gate.

## Daily rhythm

| When (Europe/Vilnius) | Action |
| --- | --- |
| 08:00 winter / 09:00 summer | Read the Telegram card. `learn` if there was a fill or a halt. |
| After any halt | Do not rearm from a laptop SQLite. Write the reason. |
| The day Phase 1 is green | Run Phase 2 the same day. Do not wait for a Monday. |
| After go | Power the gateway only with the written budget. |

## What we will not schedule

- Waiting until late 2027 because a month is empty on the calendar.
- Going live by lowering 25 bps or skipping Phase 2.
- Inbound Telegram `/buy` `/sell` as the control plane.
- A Grok supervisor that can bypass `evaluate_risk`.
- Keys in this repository.

The raised goal is a **self-funded GRAM bot as soon as the book pays**. The raised discipline is still refusing to get there by weakening the worker we already trust.
