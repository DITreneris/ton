# Operator lessons

Reusable skills. Not a quote stream. `python -m ton_gram learn` writes `learn_last_json`. This file is what we keep when a lesson is new.

## quiet_book_is_not_a_bug

A live book that stays `skip=flat` with `scope=PASS` is the working baseline. Do not lower 25 bps to manufacture fills.

Evidence **2026-11-05 10:55 Europe/Vilnius** (Railway after the `ROADMAP.md` image fix): recovery empty, halt false, SMA 8/8, mid ≈ `$1.1655` / GRAM, bid/ask **20 bps**, deviation **1 bps**. Two cycles, no candidate.

Same day **15:25–15:34 Europe/Vilnius**: still `scope=PASS`, spread **20 bps**, deviation mostly 0–7, one dip to **−17**. All `skip=flat` until the 25 bps print. The quiet stretch is still the baseline, not a broken book.

**2026-11-06 13:51–14:03 Europe/Vilnius**: halt already True, recover empty, SMA 8/8, spread 20–21 bps, deviation **−9 to +7**. Still `skip=flat`, still no candidate. A halted quiet book is the same baseline. Do not rearm to manufacture fill 2/8.

## daily_loss_marks_inventory

`daily_loss_limit` marks the **whole paper wallet** (genesis 5 000 GRAM + 5 000 USD₮), not the last clip. Floor is 1% of the $10k funding cap = **$100** = **~$0.02 / GRAM**. At `$1.16` that is about **170 bps**, not 20. A 20 000-micro mid tick is $0.02; relative to price it is ~170 bps.

Risk only evaluates this when a **candidate** exists. Flat cycles can sit under −$100 with `halt=false`. The next 25 bps print is what latches `LIMIT_BREACH`.

A sticky halt after `candidate rejected: ['daily_loss_limit']` is inventory mark when unrealized PnL is the loss driver. Do not treat it as a blown $1 fade. Do not rearm to manufacture the next fill. Do not lower 25 bps. Do not raise the all-in cap.

Evidence **2026-11-05 16:04 Europe/Vilnius**: second live candidate at deviation **−28** (`mid $1.141932` vs SMA `$1.145094`). Risk rejected `daily_loss_limit` and latched halt. The earlier clip was ~1 GRAM / ~$1.14. Morning mid was ~`$1.1655`; afternoon ~`$1.1419` (~**200 bps** cheaper). Marked inventory ≈ **−$118**. The $1 fade did not blow the cap. From 15:25 the mark was already through the floor; halt stayed false until this print.

Rearm only after an explicit policy change (mark daily loss on realized paper trades, or cut genesis GRAM so ~170 bps of book is not −$100) or after you accept that a normal GRAM day will trip this again.

## halt_reason_first_latch_wins

`latch_halt` used to overwrite the visible reason every cycle. That is why a recovered book can show `HARD_HALT` with feeds UP.

Evidence **2026-11-06 Europe/Vilnius**:

- 16:04 on 5 Nov: `LIMIT_BREACH` on `daily_loss_limit`. Operator did not rearm.
- Overnight STON.fi `POST /v1/swap/simulate` **500**s at 00:06, 01:10, 01:47, **02:14**, 03:08. Each one wrote `HARD_HALT` over the limit card.
- 02:20 check (`snap_20261106T002031Z_4424`): feeds UP, no open swap, reason `HARD_HALT`. The 500 at 02:14 had already recovered. Daily P&L **+$32.53** is 5 000 GRAM × ~$0.0065 vs **today's** UTC start-of-day, not a failed midnight reset. Confirmed at 02:02: reject reasons were `entry_halt` + `drawdown_limit` — `daily_loss_limit` was already gone.
- 06:44: next 25 bps print, `candidate rejected: ['entry_halt', 'drawdown_limit']`, reason flipped back to `LIMIT_BREACH`. Seen at 07:07 (`snap_20261106T050732Z_5500`).

Do not rearm because the reason flipped. Peak drawdown does not reset at midnight. The next candidate will latch again while equity is still ≥$300 under peak. A recovered STON.fi 500 is an **explained** feed blip for Phase 0; do not restart the 7-day clock. Do not `reset-paper`.

Hourly Grok `INCIDENT` while this halt is sticky is **one event**, not a new incident each hour. Evidence **5–6 Nov**: 18 of 24 hourly checks flagged, almost all `LIMIT_BREACH`, feeds ≥99.93%. That is the same latch. Six `NO_FLAG` hours were the pre-16:04 window. Do not rearm to make the monitor go green.

## reset_was_genesis_not_the_fill

`POST /v1/control/reset` at **2026-11-05 07:28 Europe/Vilnius** (`05:28:40Z`, HTTP 200) wiped the early halt and restored genesis balances. Recover on that boot had **no** unresolved intents (`outcomes=[]`). The 15:34 fill `int_20261105T133435Z_1971` is after that reset and still counts as fill 1/8. Do not reset again to chase a green card.

## utc_sod_mark_is_not_zero_all_day

The first successful mark after 00:00 UTC sets start-of-day equity and `daily_pnl=0`. Twenty minutes later, 5 000 GRAM × a $0.0065 tick is **+$32.53**. That is inventory mark, not a bug and not a reason to rearm.

## audit_fills_after_costs

A reserved paper intent is not a win. Score net P&L after pool fee, price impact, 50 bps conservative slip, measured delay, and measured gas.

Evidence **2026-11-05 15:34:35 Europe/Vilnius**: first live signal. Mid jumped `$1.141628` → `$1.144593` in one cycle. SMA `$1.141677`. Deviation **+25**. Risk approved. Intent `int_20261105T133435Z_1971` reserved (fade of a spike, sell GRAM). Next two cycles `skip=cooldown`. Halt stayed false. Run `POST /v1/learn` on the Railway host. Do not celebrate the print.

## dockerignore_copy_globs

If the Dockerfile `COPY`s a markdown file, `.dockerignore` must `!` that exact name. `*.md` plus `!README.md` and `!CHANGELOG.md` is not enough.

Railway failed **2026-11-05 10:54 Europe/Vilnius** with `"/ROADMAP.md": not found` while the file existed on GitHub. Local `check-image.sh` had passed because this tree already had `!ROADMAP.md`. GitHub did not. Un-ignore the file; do not drop the COPY.

A test must fail on glob excludes, not only on exact-name excludes.

## github_is_what_railway_builds

Railway builds GitHub `main`, not the laptop tree. `python -m ton_gram docs` and `COPY ROADMAP.md` can work locally and fail in the image. After a review-fix, push the same files GitHub will pack: source, `.dockerignore`, and every `COPY` path.

Each GitHub commit on `main` is a Railway boot. See `railway_boots_every_github_commit`.

## all_in_cost_gate

Fee + impact + 50 bps slip above `max_all_in_cost_bps` (80) is a reject, not a missing trade. Do not raise the cap or lower 25 bps. Inspect `rejected_candidates_by_reason`.

## recover_same_clock

Restart uses the same delay/TTL as `settle_due_intents`. Early recover is `PENDING_DELAY`. TTL lapse is `EXPIRED` (no halt card). Missing quote only is `SETTLEMENT_UNKNOWN`.

## keys_are_phase_3

Phase 2 is a paper go / no-go. Keys stay out of this tree until Phase 3 is explicitly opened.

## feed_down_is_not_warming

A STON.fi `POST /v1/swap/simulate` 500 is `skip=feed_down` and `scope=UNAVAILABLE`. It is not SMA warming and it is not `SCOPE_MISMATCH`. SMA samples stay 8/8; the next healthy cycle is `skip=flat`. Do not restart the 7-day clock on an explained blip.

Evidence **2026-11-06 12:28 and 12:37 Europe/Vilnius** (Railway CSV `09:30:33Z`–`11:45:35Z`): 509 cycles, halt True the whole window, 507 `skip=flat` / `scope=PASS`. Two simulate 500s logged `skip=warming` / `scope=FAIL` because `signal_view(mid=0)` reused the warming bucket and empty `verify_scope()` failed closed. Both recovered on the next cycle (~8–20s). Max |deviation| was **21 bps** (13:26 Vilnius). Zero candidates. That is a quiet halted book, not a broken SMA.

## railway_info_on_stderr_is_not_a_crash

Python logging defaulted to stderr. Railway labels every stderr line `severity=error`. The 6 Nov dump tagged 519 INFO cycle lines as errors. Worker INFO/WARNING now go to stdout; ERROR stays on stderr. Do not page on a Railway error count that matches the cycle count.

Confirmed **2026-11-06 13:55+ Europe/Vilnius** (image `079c099`): cycle lines are Railway `info`, not `error`.

## cycle_must_print_halt_reason

`halt=True` without `halt_reason` is not enough. The 6 Nov dump is 509 halted cycles and never says `LIMIT_BREACH` on the cycle line. Trust `open_execution_state.halt_reason` on the snapshot, and the cycle dict, not the Railway severity column.

A cycle dict that omits `halt_reason` is an **older image**, not a missing latch. Evidence **2026-11-06 13:51 Europe/Vilnius**: boot `20fdedc` (stdout logging only) printed `halt=True` with no reason. From **13:55** (`snap_20261106T115537Z_7037`, image `079c099`) every cycle is `halt_reason=LIMIT_BREACH`. Do not rearm because the field appeared. That is the same 16:04 latch from 5 Nov.

## railway_boots_every_github_commit

Railway auto-deploys **every** GitHub `main` commit. `push_files` of N files as N commits is N container restarts. Judge the **SUCCESS deploy of the tip SHA**, not the first `Starting Container` in the storm.

Evidence **2026-11-06 13:50–13:57 Europe/Vilnius**: ten GitHub commits in six minutes (`20fdedc` … `079c099`) produced ten Railway deploys. The operator paste at 13:51 was the logutil-only boot: recover empty, halt True, no `halt_reason`. Live SUCCESS is `458e438d` / `079c099`. Halt and SMA survived every restart (`outcomes=[]`, samples 8/8). Batch GitHub pushes into **one** commit when you can. Do not `reset-paper` because the box restarted.
