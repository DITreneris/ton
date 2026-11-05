# Learning loop

The Prompt Anatomy diagram is a full coding OS. This repo only wires the right-hand loop: logs → evaluate → lessons → advisory rules.

```bash
python -m ton_gram learn --day YYYY-MM-DD
```

`POST /v1/learn` does the same against the live ledger. `GET /v1/learn` returns the last pass. The next snapshot copies it under `learning`.

Rules are advisory. The worker does not auto-apply them. The loop will not lower the 25 bps entry gate or turn signing on.
