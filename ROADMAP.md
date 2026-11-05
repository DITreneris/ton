# ROADMAP — self-sustaining profitable TON GRAM bot

**Destination.** One unsupervised worker that trades **native GRAM ↔ issuer-verified USD₮** on the pinned STON.fi v2 route, pays its own gas and hosting from net edge, and stays up without daily babysitting.

**Horizon.** 5 November 2026 → 31 December 2027. January 2027 is a **go / no-go gate**, not the finish line.

**How ambition is allowed to rise.** Goals get bigger. Controls do not get looser. This repo stays paper-only and keyless until the gates below pass. Profit is net after pool fee, price impact, 50 bps slip, delay, and measured p90 gas + 25%. Win rate is not a goal.

## Current baseline (audit, 5 November 2026)

Already built and running as a paper worker:

| Capability | In repo |
| --- | --- |
| Pinned identity (mainnet, official USD₮, native GRAM, one v2 pool/router) | Yes |
| Fail-closed scope | Yes |
| Deterministic 25 bps SMA fade | Yes |
| Risk before any intent (size, reserve, stale, daily loss, drawdown, all-in cost) | Yes |
| Stressed paper fills + measured gas profile | Yes |
| Recover-first, one unresolved intent, `SETTLEMENT_UNKNOWN` | Yes |
| Isolated drills | Yes |
| Outbound Telegram, HTTP halt/rearm (no inbound commands) | Yes |
| `learn` loop, document index, `CHANGELOG.md` | Yes |
| Signing / broadcast / wallet | **Absent on purpose** |

Live book so far has been `skip=flat`. That is a working baseline, not a failed bot. There is **no proven edge** yet. That is the first thing this roadmap exists to settle.

## What “profitable” and “self-sustaining” mean

**Profitable (unit economics).** Over a rolling 30-day window, sum of net P&L after stressed (then live) costs is **strictly positive**, and average net per approved fill exceeds average all-in cost of that fill. A week of lucky prints does not count.

**Self-sustaining (ops + treasury).**

1. Railway volume, poller, and Telegram stay up with **≤1 operator hour / week**.
2. Halt, rearm, learn, and daily card work without anyone watching Grok.
3. Gas reserve (0.25 GRAM floor) is topped from **retained net edge**, not from fresh deposits.
4. Hosting is paid from that same retained edge. If the edge cannot cover a small VPS/Railway bill, the bot is not self-sustaining even if a single print is green.

**Not in scope before 2028.** Second DEX, second pair, inbound Telegram trading commands, seven AI agents, or letting Grok size or sign.

## Non-negotiables (every phase)

- No mnemonic, no signer, no `sendBoc` in **this** repository until Phase 3 is explicitly opened.
- Do not lower `STRATEGY_ENTRY_DEVIATION_BPS` from 25 to manufacture fills.
- Do not treat a healthy bid/ask as a broken book (`MID_SPREAD_MAX_BPS=200`).
- Official USD₮ master and the pinned pool/router stay fail-closed. Tickers are not identity.
- Grok / `MONITOR_RESULT` / `learn` stay **advisory**. They cannot authorize an intent.
- Telegram stays outbound. Kill switch is `POST /v1/control/halt` on the running instance.
- One pair, one router. Teleton-style multi-DEX is a 2028 question.

## Phase 0 — Evidence factory

**5 Nov 2026 → 30 Nov 2026**

Goal: the worker is a reliable instrument, not a hope.

- Keep Railway paper up. Rotate any bearer that was ever pasted into chat.
- Daily: Telegram card (08:00 Europe/Vilnius winter) + `POST /v1/learn` (or `python -m ton_gram learn`).
- Weekly: isolated `drill all` against the running host. Re-run `measure-gas` if the bundled profile is older than 30 days.
- File every learn pass into the document catalog when a lesson is new (`docs sync` after an operator note).
- **Exit.** 26 consecutive UTC days of ledger (feed uptime floor ≥99%, no unexplained `HARD_HALT`). Flat book is acceptable. Missing days are not.

No strategy rewrite in this phase.

## Phase 1 — Edge verdict

**1 Dec 2026 → 31 Dec 2026**

Goal: answer whether the **current** 25 bps fade has a net edge after costs.

Score the month on the live ledger, not on a backtest rewrite:

| Result | Meaning | Next |
| --- | --- | --- |
| ≥8 approved paper fills **and** 30-day net > 0 after stressed costs | Edge candidate | Phase 2 go-review |
| Candidates emit, all rejected by risk | Risk is doing its job; inspect reasons, do not loosen caps | Stay in Phase 1 / 2 paper |
| 0 candidates, almost all `skip=flat` | Book has no 25 bps excursion. That is data. | Phase 2 **no-go on this signal** |
| Spread or halt dominates | Instrumentation or feed problem | Fix worker, do not go live |

Optional paper work **only if** the table says the signal is dead, not “slow”:

- Add a second **paper-only** signal family (for example a measured inventory / flow feature) behind the **same** risk engine. Do not replace 25 bps with a looser fade.
- Keep both signals scored separately in `learn`.

Still no keys.

## Phase 2 — January 2027 go / no-go

**1 Jan 2027 → 31 Jan 2027**

This month decides whether 2027 is a live year or another paper year.

### Go (all must be true)

1. Phase 0 uptime met.
2. Phase 1 table is “edge candidate” on the **pinned** 25 bps fade (or on a second paper signal that beat it on the same cost model).
3. Isolated drills green on the production host in the last 14 days.
4. Restart recovery drill: kill the Railway process mid-intent, confirm no double fill and a terminal state.
5. Adversarial risk: stale quote, scope mismatch, daily-loss, drawdown — all reject or halt as designed.
6. Written live budget: first live clip ≤ **$10**, hard cap still **$50**, funding still **$10k** paper-equivalent, 2% position cap.
7. Isolated **execution gateway** designed as a **separate** service (new repo or new process). This worker remains the only brain. The gateway holds keys. This tree still does not.

### No-go (any one is enough)

- No net-positive 30-day paper window after costs.
- Any unexplained `SETTLEMENT_UNKNOWN` or feed-down that was “reamed away” without a root cause.
- Desire to go live by lowering 25 bps, widening size, or adding a second DEX.

**No-go action.** Stay paper through Q1 2027. Either retire this signal in `learn` as `quiet_book_is_not_a_bug`, or iterate a **new** paper signal. Do not open Phase 3.

**Go action.** Freeze policy version. Open Phase 3 with a dated decision note in `CHANGELOG.md` and `docs/roadmap.md`.

## Phase 3 — Micro live (only if go)

**1 Feb 2027 → 30 Apr 2027**

Ambition: the first real GRAM↔USD₮ clip that is still green after on-chain costs.

- Execution gateway: sign and send **only** what this worker already approved. Bind the exact quote id, pool, router, size, and min-out. Unknown settlement **blocks retries**.
- This repo: add a live-intent status projection (read-only TonAPI/Toncenter), keep paper twin in parallel, same risk.
- First 10 live fills: max $10, then stop and score. If net ≤ 0 after real gas and refunds, revert to paper (Phase 1).
- Telegram: still outbound. Halt remains HTTP. A down Grok must not freeze the kill switch.
- Operator: one human approval for the **gateway power-on**. Not per-trade chat commands.

Self-sustain preview: log Railway invoice vs net. If live net cannot cover a month of hosting after 30 days, stay micro — do not scale.

## Phase 4 — Self-sustain

**1 May 2027 → 31 Aug 2027**

Ambition: the bot funds itself.

- Raise live clip toward $50 **only** from retained net, never from new deposits.
- Auto-top the 0.25 GRAM gas floor from GRAM inventory earned, not from USD₮ buys that erase the edge.
- Quarterly `measure-gas`. If p90 net cost moves more than 25%, freeze size until `learn` re-scores.
- Target: 30-day live net ≥ hosting + measured gas + a 2× safety margin. That is “self-sustaining.”
- Still one pair. Still no Grok authority.

## Phase 5 — Hold or scale inside the same box

**1 Sep 2027 → 31 Dec 2027**

Ambition: if unit economics still hold at $50, keep them. Do not confuse volume with profit.

- Scale rule: increase max trade only when the last 60 live days are net-positive **at the current size**. Step is +$25, cap remains 2% of funding.
- If edge fades (regime change, fee, or gas), step down automatically in policy — `learn` may **recommend**, operator applies.
- 2028 parking lot only: second pair, LP inventory, or a second venue. Not this year.

31 December 2027 success looks like one of:

- **A.** Self-sustaining live bot, one route, keys still outside this tree, documented go decision.
- **B.** Honest paper year: no edge at 25 bps, no live capital burned, a written “signal retired” lesson. That is a successful use of this worker.

Burning money to look ambitious is not on the map.

## Weekly rhythm (all phases)

| When (Europe/Vilnius) | Action |
| --- | --- |
| Daily 08:00 winter / 09:00 summer | Read the Telegram card. Do nothing if `flat` and scope `PASS`. |
| After any halt | Do not rearm from a laptop SQLite. Use the running host. Write the reason. |
| After any approved / live fill | `learn` that UTC day. Score net, not wins. |
| Sunday | `docs sync` if policy or this roadmap changed. Isolated drill if Phase 1+. |

## What we will not schedule

- Cloning HyperGrok, TonShield, or Teleton into this repo.
- Inbound Telegram `/buy` `/sell` `/kill` as the control plane.
- `@ston-fi/sdk` TypeScript next to this Python worker.
- A Grok strategy supervisor that can bypass `evaluate_risk`.
- “Just go live in January” without Phase 2.

The raised goal is a **self-funded GRAM bot by late 2027 if the book pays**. The raised discipline is refusing to get there by weakening the worker we already trust.
