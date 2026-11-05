# Paper policy

This worker is paper-mode only. It does not hold keys, does not sign, and does not broadcast.

Destination (see `ROADMAP.md`): a self-sustaining profitable GRAM↔USD₮ bot **as soon as the book pays**. Ambition does not relax these gates. Live signing stays out of this tree until **Phase 3** is explicitly opened after a Phase 2 go. Phase 2 is a same-day paper go / no-go, not a waiting month.

## Gates that stay put

- Entry deviation: **25 bps**. A quiet book is the working baseline. Do not lower this to manufacture fills.
- Mid spread: **200 bps**. A healthy bid/ask is about 2× pool fee, often 30–50 bps. That is not a broken book.
- Max trade: **50 USD₮**. Size the USD₮ leg from last mid × 1 GRAM, never from the GRAM ask.
- All-in cost: **80 bps** = pool fee + price impact + **50 bps** conservative slip. If fee+impact+50 > 80, risk rejects with `all_in_cost`. That is the gate working. Do not raise the cap or lower 25 bps to manufacture approvals.
- Daily summary hour: **06:00 UTC** (08:00 Europe/Vilnius in winter).

## Recover and settlement

Restart recovery uses the same clock as `settle_due_intents`:

- Delay not elapsed → leave `RESERVED` (`PENDING_DELAY`). The running worker fills when `delay_stress_s` is due.
- Quote missing → `RECONCILED_UNKNOWN` and latch `SETTLEMENT_UNKNOWN`.
- Past `intent_ttl_s` (default 45 s) → `EXPIRED`. That is not `SETTLEMENT_UNKNOWN` and does not page a halt card.

## Halt

Sticky halt reasons: `OPERATOR`, `SCOPE_MISMATCH`, `LIMIT_BREACH`, `HARD_HALT`, `SETTLEMENT_UNKNOWN`. Rearm is manual. A feed-down keeps `HARD_HALT` and does not overwrite it with `SCOPE_MISMATCH`.

`daily_loss_limit` (and then `LIMIT_BREACH`) is **marked equity of the genesis paper wallet**, not realized clip P&L. Default inventory is 5 000 GRAM. 1% of the $10k funding cap is $100 = **~$0.02 / GRAM** (~**170 bps** at $1.16). Risk only sees this on the next candidate. Flat cycles can sit under the floor with `halt=false`. See `daily_loss_marks_inventory`. Do not rearm to manufacture fills.

## Learn and reset

`learn` scores the live `Settings` into `rules_used`. It still forces `signing=false` and will not recommend lowering 25 bps or raising `max_all_in_cost_bps`. `reset-paper` wipes fills and also clears `learn_last_json` so a reset day does not keep yesterday’s score.

## Telegram

Outbound only. No inbound commands. Record an alert only after a successful send.
