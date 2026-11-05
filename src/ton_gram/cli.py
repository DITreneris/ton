from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from pathlib import Path

import uvicorn

from ton_gram.api import create_app
from ton_gram.clock import Clock
from ton_gram.config import Settings, reset_settings_cache
from ton_gram.ledger import Ledger
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
    app = create_app(settings, ledger, clock)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
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
    }
    return commands[args.cmd](args)
