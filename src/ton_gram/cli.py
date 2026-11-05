from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from ton_gram.alerts import TelegramAlerter
from ton_gram.api import create_app
from ton_gram.clock import Clock, FrozenClock
from ton_gram.config import Settings, reset_settings_cache
from ton_gram.logutil import configure_logging
from ton_gram.drill import SCENARIOS, isolated_harness, remote_run, run_all
from ton_gram.ledger import Ledger
from ton_gram.measure import (
    GasMeasurer,
    apply_profile,
    build_profile,
    default_csv_path,
    default_profile_path,
    write_csv,
    write_profile,
)
from ton_gram.poller import MarketPoller
from ton_gram.report import build_daily_report
from ton_gram.scope import verify_scope
from ton_gram.validator import validate_monitor_result
from ton_gram.worker import PaperWorker


def _boot(settings: Settings | None = None) -> tuple[Settings, Clock, Ledger]:
    reset_settings_cache()
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    ledger = Ledger(settings, clock)
    return settings, clock, ledger


def cmd_worker(args: argparse.Namespace) -> int:
    settings, clock, ledger = _boot()
    worker = PaperWorker(settings, clock, ledger)
    app = create_app(settings, ledger, clock, worker.alerter)
    thread = threading.Thread(target=worker.run_forever, name="paper-worker", daemon=True)
    thread.start()
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    finally:
        worker.stop()
        worker.close()
        ledger.close()
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    settings, clock, ledger = _boot()
    worker = PaperWorker(settings, clock, ledger)
    try:
        summary = worker.cycle()
        print(json.dumps(summary, indent=2))
        if args.snapshot_out and worker.last_snapshot:
            path = Path(args.snapshot_out)
            path.parent.mkdir(parents=True, exist_ok=True)
            redacted = dict(worker.last_snapshot)
            path.write_text(json.dumps(redacted, indent=2) + "\n")
            print(f"wrote {path}", file=sys.stderr)
    finally:
        worker.close()
        ledger.close()
    return 0


def cmd_verify_scope(args: argparse.Namespace) -> int:
    settings, clock, _ledger = _boot()
    poller = MarketPoller(settings, clock)
    try:
        inputs = poller.fetch_scope_inputs()
        quotes = poller.fetch_quotes(inputs["pool"])
        scope = verify_scope(
            settings,
            observed_network=inputs.get("observed_network"),
            usdt_asset=inputs.get("usdt_asset"),
            native_asset=inputs.get("native_asset"),
            pool=inputs.get("pool"),
            router=inputs.get("router"),
            quotes=[q.raw | {"router_address": q.router_address, "pool_address": q.pool_address} for q in quotes.values()],
        )
        print(json.dumps(scope.__dict__, indent=2, default=lambda o: o.__dict__))
        return 0 if scope.passed else 2
    finally:
        poller.close()


def cmd_validate(args: argparse.Namespace) -> int:
    settings, clock, ledger = _boot()
    payload = json.loads(Path(args.result_json).read_text())
    snapshot = None
    if args.snapshot_json:
        snapshot = json.loads(Path(args.snapshot_json).read_text())
    elif payload.get("snapshot_id"):
        snapshot = ledger.get_snapshot(payload["snapshot_id"]) or ledger.latest_snapshot()
    result = validate_monitor_result(payload, snapshot=snapshot, settings=settings, clock=clock)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.accepted else 2


def cmd_report(args: argparse.Namespace) -> int:
    settings, clock, ledger = _boot()
    report = build_daily_report(ledger, clock, args.day)
    print(json.dumps(report, indent=2))
    ledger.close()
    return 0


def cmd_rearm(args: argparse.Namespace) -> int:
    _settings, _clock, ledger = _boot()
    ledger.rearm()
    print(json.dumps({"entry_halt": ledger.entry_halt}))
    ledger.close()
    return 0


def cmd_halt(args: argparse.Namespace) -> int:
    _settings, _clock, ledger = _boot()
    ledger.latch_halt("OPERATOR")
    print(json.dumps({"entry_halt": ledger.entry_halt, "reason": ledger.halt_reason}))
    ledger.close()
    return 0


def cmd_measure_gas(args: argparse.Namespace) -> int:
    settings, clock, ledger = _boot()
    measurer = GasMeasurer(settings)
    try:
        samples = measurer.collect(target=args.target)
    finally:
        measurer.close()
    if len(samples) < 10:
        print(json.dumps({"error": "insufficient samples", "n": len(samples)}), file=sys.stderr)
        return 2
    profile = build_profile(samples, settings)
    apply_profile(settings, profile)
    ledger.save_gas_profile(profile)
    csv_path = Path(args.csv) if args.csv else default_csv_path(settings)
    profile_path = Path(args.profile) if args.profile else default_profile_path(settings)
    write_csv(samples, csv_path)
    write_profile(profile, profile_path)
    artifact_csv = Path("artifacts/gas_refunds.csv")
    artifact_profile = Path("artifacts/gas_profile.json")
    write_csv(samples, artifact_csv)
    write_profile(profile, artifact_profile)
    print(json.dumps({"csv": str(csv_path), "profile": str(profile_path), **{k: profile[k] for k in ("sample_n", "directions", "net_cost_gram_nano", "latency_s", "applied")}}, indent=2))
    ledger.close()
    return 0


def cmd_drill(args: argparse.Namespace) -> int:
    scenario = args.scenario or "all"
    if args.base_url:
        token = args.token or Settings().drill_token
        if not token:
            print("TON_GRAM_DRILL_TOKEN is required for remote drills", file=sys.stderr)
            return 2
        result = remote_run(args.base_url, token, scenario)
        print(json.dumps(result, indent=2))
        return 0 if result.get("passed", True) else 2

    settings = Settings()
    settings.drill_token = args.token or settings.drill_token or "local-drill-token"
    if args.isolated:
        settings.data_dir = Path(tempfile.mkdtemp(prefix="ton-gram-drill-"))
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    clock = FrozenClock()
    paper = Ledger(settings, clock)
    if args.http:
        app = create_app(settings, paper, clock)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {settings.drill_token}"}
        path = "/v1/drill/all" if scenario == "all" else f"/v1/drill/{scenario}"
        response = client.post(path, headers=headers)
        result = response.json()
        ok = response.status_code == 200 and result.get("passed", True)
    else:
        harness = isolated_harness(settings, clock)
        result = run_all(harness) if scenario == "all" else harness.run(scenario)
        if scenario != "all":
            result = {"namespace": "drill", "paper_untouched": True, **result}
        harness.ledger.close()
        ok = bool(result.get("passed"))
    print(json.dumps(result, indent=2, default=str))
    paper.close()
    return 0 if ok else 2


def cmd_reset_paper(args: argparse.Namespace) -> int:
    settings, _clock, ledger = _boot()
    result = ledger.reset_paper(reason="OPERATOR")
    print(json.dumps(result, indent=2))
    ledger.close()
    return 0


def cmd_telegram_test(args: argparse.Namespace) -> int:
    settings, clock, ledger = _boot()
    alerter = TelegramAlerter(settings, ledger)
    text = args.message or "TON GRAM paper worker telegram-test: outbound only, no commands."
    result = alerter.send("telegram_test", text, force=True)
    print(json.dumps(result, indent=2, default=str))
    ledger.close()
    return 0 if not result.get("skipped") else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TON GRAM paper-mode worker")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("worker", help="run poller + read-only snapshot HTTP server")
    once = sub.add_parser("once", help="run a single paper cycle against mainnet APIs")
    once.add_argument("--snapshot-out", default="")
    sub.add_parser("verify-scope", help="fail-closed live scope check")
    val = sub.add_parser("validate-result", help="validate a MONITOR_RESULT JSON file")
    val.add_argument("result_json")
    val.add_argument("--snapshot-json", default="")
    rep = sub.add_parser("report", help="print the daily paper report")
    rep.add_argument("--day", default=None)
    sub.add_parser("halt", help="latch sticky entry halt")
    sub.add_parser("rearm", help="explicit operator rearm")
    sub.add_parser("reset-paper", help="wipe paper fills/intents and restore starting balances")
    measure = sub.add_parser("measure-gas", help="read-only mainnet gas/refund/latency sample")
    measure.add_argument("--target", type=int, default=60)
    measure.add_argument("--csv", default="")
    measure.add_argument("--profile", default="")
    drill = sub.add_parser("drill", help="run recovery drills against local ledger or a running instance")
    drill.add_argument("scenario", nargs="?", default="all", choices=["all", *SCENARIOS])
    drill.add_argument("--base-url", default="", help="running instance, e.g. https://app.up.railway.app")
    drill.add_argument("--token", default="", help="TON_GRAM_DRILL_TOKEN")
    drill.add_argument("--http", action="store_true", help="exercise FastAPI drill routes in-process")
    drill.add_argument("--isolated", action="store_true", help="use a temp ledger so paper state is untouched")
    tg = sub.add_parser("telegram-test", help="send one outbound Telegram test message")
    tg.add_argument("--message", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "worker": cmd_worker,
        "once": cmd_once,
        "verify-scope": cmd_verify_scope,
        "validate-result": cmd_validate,
        "report": cmd_report,
        "halt": cmd_halt,
        "rearm": cmd_rearm,
        "reset-paper": cmd_reset_paper,
        "measure-gas": cmd_measure_gas,
        "drill": cmd_drill,
        "telegram-test": cmd_telegram_test,
    }
    return commands[args.cmd](args)
