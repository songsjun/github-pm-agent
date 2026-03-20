from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from github_pm_agent.app import GitHubPMAgentApp
from github_pm_agent.config import load_config, project_root, runtime_dir
from github_pm_agent.queue_store import QueueStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="github-pm-agent")
    parser.add_argument("--config", required=True, help="Path to a JSON config file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("poll", help="Poll GitHub and enqueue new events")

    cycle_parser = subparsers.add_parser("cycle", help="Poll GitHub and process the queue")
    cycle_parser.add_argument(
        "--loop",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Run in daemon mode, repeating every N seconds (0 = run once)",
    )

    queue_parser = subparsers.add_parser("queue", help="Inspect the local queue")
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command", required=True)
    queue_subparsers.add_parser("list", help="List pending events")
    peek_parser = queue_subparsers.add_parser("peek", help="Peek pending events")
    peek_parser.add_argument("--limit", type=int, default=5)

    return parser


def _app_from_args(args: Any) -> GitHubPMAgentApp:
    config = load_config(args.config)
    return GitHubPMAgentApp(config, project_root(config))


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    app = _app_from_args(args)

    if args.command == "poll":
        print(json.dumps(app.poll(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "cycle":
        if args.loop:
            print(f"[daemon] starting loop every {args.loop}s — Ctrl-C to stop", flush=True)
            while True:
                result = app.cycle()
                print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
                time.sleep(args.loop)
        else:
            print(json.dumps(app.cycle(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "queue":
        queue = QueueStore(runtime_dir(app.config))
        if args.queue_command == "list":
            payload = [event.to_dict() for event in queue.list_pending()]
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if args.queue_command == "peek":
            payload = [event.to_dict() for event in queue.peek(limit=args.limit)]
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

    parser.error("unknown command")
    return 1

