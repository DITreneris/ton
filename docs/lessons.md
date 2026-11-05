# Operator lessons

Reusable skills. Not a quote stream. `python -m ton_gram learn` writes `learn_last_json`. This file is what we keep when a lesson is new.

## quiet_book_is_not_a_bug

A live book that stays `skip=flat` with `scope=PASS` is the working baseline. Do not lower 25 bps to manufacture fills.

Evidence **2026-11-05 10:55 Europe/Vilnius** (Railway after the `ROADMAP.md` image fix): recovery empty, halt false, SMA 8/8, mid ≈ `$1.1655` / GRAM, bid/ask **20 bps**, deviation **1 bps**. Two cycles, no candidate. Do nothing.

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
