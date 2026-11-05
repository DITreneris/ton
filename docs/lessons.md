# Operator lessons

Reusable skills. Not a quote stream. `python -m ton_gram learn` writes `learn_last_json`. This file is what we keep when a lesson is new.

## quiet_book_is_not_a_bug

A live book that stays `skip=flat` with `scope=PASS` is the working baseline. Do not lower 25 bps to manufacture fills.

Evidence **2026-11-05 10:55 Europe/Vilnius** (Railway after the `ROADMAP.md` image fix): recovery empty, halt false, SMA 8/8, mid ≈ `$1.1655` / GRAM, bid/ask **20 bps**, deviation **1 bps**. Two cycles, no candidate.

Same day **15:25–15:34 Europe/Vilnius**: still `scope=PASS`, spread **20 bps**, deviation mostly 0–7, one dip to **−17**. All `skip=flat` until the 25 bps print. The quiet stretch is still the baseline, not a broken book.

## daily_loss_marks_inventory

`daily_loss_limit` marks the **whole paper wallet** (genesis 5 000 GRAM + 5 000 USD₮), not the last clip. Floor is 1% of the $10k funding cap = **$100**. On 5 000 GRAM that is about **20 bps** of GRAM vs the UTC start-of-day mark.

A sticky `LIMIT_BREACH` after `candidate rejected: ['daily_loss_limit']` is inventory mark. Do not treat it as a blown $1 fade. Do not rearm to manufacture the next fill. Do not lower 25 bps. Do not raise the all-in cap.

Evidence **2026-11-05 16:04 Europe/Vilnius**: second live candidate at deviation **−28** (`mid $1.141932` vs SMA `$1.145094`). Risk rejected `daily_loss_limit` and latched halt. The earlier clip was ~1 GRAM / ~$1.14. Morning mid was ~`$1.1655`; afternoon ~`$1.1419` (~ **200 bps** cheaper). Marked inventory ≈ **−$118**. The $1 fade did not blow the cap.

Rearm only after an explicit policy change (mark daily loss on realized paper trades, or cut genesis GRAM so 20 bps of book is not −$100) or after you accept that a normal GRAM day will trip this again.

## audit_fills_after_costs

A reserved paper intent is not a win. Score net P&L after pool fee, price impact, 50 bps conservative slip, measured delay, and measured gas.

Evidence **2026-11-05 15:34:35 Europe/Vilnius**: first live signal. Mid jumped `$1.141628` → `$1.144593` in one cycle. SMA `$1.141677`. Deviation **+25**. Risk approved. Intent `int_20261105T133435Z_1971` reserved (fade of a spike, sell GRAM). Next two cycles `skip=cooldown`. Halt stayed false. Run `POST /v1/learn` on the Railway host. Do not celebrate the print.

## dockerignore_copy_globs

If the Dockerfile `COPY`s a markdown file, `.dockerignore` must `!` that exact name. `*.md` plus `!README.md` and `!CHANGELOG.md` is not enough.

Railway failed **2026-11-05 10:54 Europe/Vilnius** with `"/ROADMAP.md": not found` while the file existed on GitHub. Local `check-image.sh` had passed because this tree already had `!ROADMAP.md`. GitHub did not. Un-ignore the file; do not drop the COPY.

A test must fail on glob excludes, not only on exact-name excludes.

## github_is_what_railway_builds

Railway builds GitHub `main`, not the laptop tree. `python -m ton_gram docs` and `COPY ROADMAP.md` can work locally and fail in the image. After a review-fix, push the same files GitHub will pack: source, `.dockerignore`, and every `COPY` path.

## all_in_cost_gate

Fee + impact + 50 bps slip above `max_all_in_cost_bps` (80) is a reject, not a missing trade. Do not raise the cap or lower 25 bps. Inspect `rejected_candidates_by_reason`.

## recover_same_clock

Restart uses the same delay/TTL as `settle_due_intents`. Early recover is `PENDING_DELAY`. TTL lapse is `EXPIRED` (no halt card). Missing quote only is `SETTLEMENT_UNKNOWN`.

## keys_are_phase_3

Phase 2 is a paper go / no-go. Keys stay out of this tree until Phase 3 is explicitly opened.
