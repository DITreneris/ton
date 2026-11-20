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

Sticky halt reasons: `OPERATOR`, `SCOPE_MISMATCH`, `LIMIT_BREACH`, `HARD_HALT`, `SETTLEMENT_UNKNOWN`. Rearm is manual. **First reason wins until rearm.** A later STON.fi 500 is recorded as `latch_ignored` and must not rewrite `halt_reason`. `SETTLEMENT_UNKNOWN` is the only upgrade.

The Grok snapshot maps `HARD_HALT`/`OPERATOR` → `reason_code=HARD_HALT` and every other latched reason → `LIMIT_BREACH`. Read `open_execution_state.halt_reason`, not only the envelope.

Halt floors are **realized clip P&L**. See `risk_floors_are_realized`.

- `daily_loss_limit` when `day_realized_pnl <= -$100` (1% of $10k). UTC day still resets. Marked `daily_pnl_usdt_micro` is telemetry.
- `drawdown_limit` when realized clip equity is **$300** under `peak_clip_equity`. Peak clip equity ratchets **only on fills**. Marked 5 000 GRAM vs peak is telemetry (`drawdown_usdt_micro` on the cycle). Genesis inventory still marks; it no longer latches.
- Cycle line prints both: `daily_pnl_usdt_micro` / `drawdown_usdt_micro` (marks) and `day_realized_usdt_micro` / `realized_drawdown_usdt_micro` (floors). Operator reads **realized** for rearm.

The 5 Nov `LIMIT_BREACH` was marked inventory (`daily_loss_marks_inventory`): −$118 on 5 000 GRAM vs a $1 fade. That latch is still sticky until **rearm after this ships**. Do not `reset-paper` to chase a green card.

`daily_pnl` (marked) and `day_realized` both reset on the first mark of the new UTC day. **Marked drawdown vs peak equity does not.** Peak marked equity still ratchets on a new high while `entry_halt` is True — halt blocks clips, not marks. See `halt_does_not_freeze_peak`. A green daily P&L after midnight does not make rearm safe **until this image is live**; after it is live, read realized drawdown, not today's mark. See `read_drawdown_not_daily_pnl`.

A 25 bps print while halted is a rejected candidate (`entry_halt`, and `drawdown_limit` / `daily_loss_limit` only if **realized** floors are through), not fill 2/8. Cycle skip is `halted`, not `emitted`. See `halted_print_is_not_fill_two`.

After Railway SUCCESS of the tip SHA: snapshot still shows the old `LIMIT_BREACH` and ~$1.2k marked hole with realized ~0. Then `POST /v1/control/rearm`. Do **not** `reset-paper`. Next fill is 2/8, not a win. `POST /v1/learn`. If the first live day after rearm burns $100 realized or $300 realized from clip peak, do not rearm to manufacture the rest of the 8.

A provider 500 is `skip=feed_down` and `scope=UNAVAILABLE`. It must not look like SMA `warming` or `SCOPE_MISMATCH`. Cycle logs include `halt_reason`. Worker INFO **and WARNING** go to stdout so Railway does not tag a healthy cycle as `severity=error`. Filter STON.fi 500s on `provider failure` in the message, not the Railway error column.

Railway boots every GitHub `main` commit. Score the SUCCESS deploy of the tip SHA. A cycle line missing `halt_reason` is an older image, not a missing latch. Do not reset-paper because the box restarted.

## Learn and reset

`learn` scores the live `Settings` into `rules_used`. It still forces `signing=false` and will not recommend lowering 25 bps or raising `max_all_in_cost_bps`. Phase 1 net is fill shortfall after fee, impact, 50 slip, delay, **and gas**. Gas is not in the 80 bps reject. A 25 bps 2-minute fade vs ~80–135 bps stressed cost is expected to fail `net > 0`. `reset-paper` wipes fills (including `int_20261105T133435Z_1971`) and also clears `learn_last_json`. Do not reset-paper to chase a green card.

## Telegram

Outbound only. No inbound commands. Record an alert only after a successful send. A successful daily card does not log a send-failure note. If Railway ever printed a full `api.telegram.org/bot…` URL, rotate `TON_GRAM_TELEGRAM_BOT_TOKEN`.
