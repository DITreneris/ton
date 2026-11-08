# Changelog

All notable changes to the TON GRAM paper worker and its document set.

Document versions are independent of the Python package version.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 2026-11-05

### Added

- Lean document index, per-document versioning, and this changelog.
- Closed learning loop: `python -m ton_gram learn` scores a paper day and writes advisory lessons. No auto-apply, no 25 bps retune.
- `ROADMAP.md`: destination is a self-funded profitable GRAM bot by late 2027. January 2027 is go / no-go, not a launch.

### Changed

- Live `flat` cycles stay `flat`: size-matched USD₮ mid, `mid_spread_max_bps=200`, feed-down keeps `HARD_HALT`.
- Paper policy: live signing is Phase 3, not Phase 2. `learn` scores live Settings. All-in cap (80 bps) is documented, not raised.

### Fixed

- `recover()` no longer fills before `delay_stress_s`. TTL expire is `EXPIRED`, not `SETTLEMENT_UNKNOWN`.
- `reset-paper` clears `learn_last_json`.
- `/healthz` no longer advertises `drill_enabled`.
- Railway image: `.dockerignore` un-ignores `ROADMAP.md` so `COPY ROADMAP.md` is in the build context.

### Documents
- `document-system` v1 added: initial catalog
- `learning-loop` v1 added: initial catalog
- `paper-policy` v1 added: initial catalog
- `pinned-route` v1 added: initial catalog
- `document-system` v2 changed: 2027 self-sustain roadmap
- `paper-policy` v2 changed: 2027 self-sustain roadmap
- `roadmap` v1 added: 2027 self-sustain roadmap
- `document-system` v3 changed: review-fix: Phase 3 keys, all-in cap, recover delay, live learn rules
- `learning-loop` v2 changed: review-fix: Phase 3 keys, all-in cap, recover delay, live learn rules
- `paper-policy` v3 changed: review-fix: Phase 3 keys, all-in cap, recover delay, live learn rules
- `roadmap` v2 changed: review-fix: Phase 3 keys, all-in cap, recover delay, live learn rules
- `document-system` v4 changed: file operator lessons: dockerignore glob, live flat book, Railway=GitHub
- `learning-loop` v3 changed: file operator lessons: dockerignore glob, live flat book, Railway=GitHub
- `lessons` v1 added: file operator lessons: dockerignore glob, live flat book, Railway=GitHub
- `learning-loop` v4 changed: file daily_loss_marks_inventory: first live fade + inventory-mark halt
- `lessons` v2 changed: file daily_loss_marks_inventory: first live fade + inventory-mark halt
- `paper-policy` v4 changed: file daily_loss_marks_inventory: first live fade + inventory-mark halt
- `roadmap` v3 changed: file daily_loss_marks_inventory: first live fade + inventory-mark halt
- `learning-loop` v5 changed: correct daily-loss math: ~170 bps / $0.02 per GRAM; halt only on candidate
- `lessons` v3 changed: correct daily-loss math: ~170 bps / $0.02 per GRAM; halt only on candidate
- `paper-policy` v5 changed: correct daily-loss math: ~170 bps / $0.02 per GRAM; halt only on candidate
- `roadmap` v4 changed: correct daily-loss math: ~170 bps / $0.02 per GRAM; halt only on candidate
- `document-system` v5 changed: evidence-gated roadmap: open the next phase the day the table is green
- `paper-policy` v6 changed: evidence-gated roadmap: open the next phase the day the table is green
- `roadmap` v5 changed: evidence-gated roadmap: open the next phase the day the table is green

## 2026-11-04

### Added

- Daily Telegram card at 06:00 UTC (08:00 Europe/Vilnius in winter).
- Measured gas profile in the image; isolated recovery drills.

### Fixed

- `counts()` binding, Telegram 404 burning the daily key, SMA reset on deploy, first-cycle missed heartbeat.

## 2026-11-06

### Fixed

- Feed-down cycles skip as `feed_down` with `scope=UNAVAILABLE`, not SMA `warming` / `SCOPE_MISMATCH`.
- Cycle summary includes `halt_reason`.
- Worker INFO/WARNING log to stdout so Railway does not tag healthy cycles as `severity=error`.

### Documents

- `learning-loop` v6 changed: first latch wins; drawdown does not reset at UTC midnight; do not rearm on HARD_HALT flip
- `lessons` v4 changed: first latch wins; drawdown does not reset at UTC midnight; do not rearm on HARD_HALT flip
- `paper-policy` v7 changed: first latch wins; drawdown does not reset at UTC midnight; do not rearm on HARD_HALT flip
- `roadmap` v6 changed: first latch wins; drawdown does not reset at UTC midnight; do not rearm on HARD_HALT flip
- `lessons` v5 changed: sticky halt is one incident; 07:28 Vilnius reset was paper genesis
- `paper-policy` v8 changed: sticky halt is one incident; 07:28 Vilnius reset was paper genesis
- `lessons` v6 changed: feed_down is not warming; Railway stderr is not a crash; cycle prints halt_reason
- `paper-policy` v9 changed: feed_down is not warming; Railway stderr is not a crash; cycle prints halt_reason
- `lessons` v7 changed: live LIMIT_BREACH on cycle line; Railway boots every GitHub commit
- `paper-policy` v10 changed: live LIMIT_BREACH on cycle line; Railway boots every GitHub commit

## 2026-11-08

### Documents

- `lessons` v8 changed: halted 25 bps print is not fill 2/8
- `paper-policy` v11 changed: halted 25 bps print is not fill 2/8
- `learning-loop` v7 changed: halted 25 bps print is not fill 2/8
