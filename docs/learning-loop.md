# Learning loop

The Prompt Anatomy diagram is a full coding OS. This repo only wires the right-hand loop: logs → evaluate → lessons → advisory rules.

```bash
python -m ton_gram learn --day YYYY-MM-DD
```

`POST /v1/learn` does the same against the live ledger. `GET /v1/learn` returns the last pass. The next snapshot copies it under `learning`.

`rules_used` is copied from the running `Settings` (spread, size, all-in cap, slip, intent TTL). Signing stays false. Recommended updates have `"apply": false`. The loop will not lower the 25 bps entry gate, will not raise `max_all_in_cost_bps`, and will not turn signing on.

If candidates emit and `rejected_candidates_by_reason.all_in_cost` dominates, the lesson is `all_in_cost_gate`. That means fee + impact + 50 bps slip exceeded 80 bps. Inspect the quotes. Do not loosen the entry gate.

`reset-paper` clears `learn_last_json`. Learn does **not** write the document catalog. File a new lesson in `docs/lessons.md` and `docs sync`.

A quiet/flat book is scored as the working baseline, not as a bug to manufacture fills.
