# Paper policy

This worker is paper-mode only. It does not hold keys, does not sign, and does not broadcast.

## Gates that stay put

- Entry deviation: **25 bps**. A quiet book is the working baseline. Do not lower this to manufacture fills.
- Mid spread: **200 bps**. A healthy bid/ask is about 2× pool fee, often 30–50 bps. That is not a broken book.
- Max trade: **50 USD₮**. Size the USD₮ leg from last mid × 1 GRAM, never from the GRAM ask.
- Daily summary hour: **06:00 UTC** (08:00 Europe/Vilnius in winter).

## Halt

Sticky halt reasons: `OPERATOR`, `SCOPE_MISMATCH`, `LIMIT_BREACH`, `HARD_HALT`, `SETTLEMENT_UNKNOWN`. Rearm is manual. A feed-down keeps `HARD_HALT` and does not overwrite it with `SCOPE_MISMATCH`.

## Telegram

Outbound only. No inbound commands. Record an alert only after a successful send.
