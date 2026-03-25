from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from github_pm_agent.app import GitHubPMAgentApp
from github_pm_agent.config import load_config, project_root, runtime_dir
from github_pm_agent.queue_store import QueueStore


def _add_queue_list_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int)
    parser.add_argument("--event-id")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="github-pm-agent")
    parser.add_argument("--config", required=True, help="Path to a JSON config file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser(
        "start",
        help="Bootstrap a new GitHub project from a requirements file",
    )
    start_parser.add_argument(
        "--requirements",
        required=True,
        help="Path to the raw requirements markdown file",
    )

    subparsers.add_parser(
        "release",
        help="Generate README and development report, then create a release PR",
    )

    subparsers.add_parser("poll", help="Poll GitHub and enqueue new events")
    subparsers.add_parser("cycle", help="Poll GitHub and process the queue")
    subparsers.add_parser("reconcile", help="Process pending queue items without polling")
    subparsers.add_parser("analytics", help="Report runtime, memory, and action analytics")

    daemon_parser = subparsers.add_parser("daemon", help="Run poll/process in a loop")
    daemon_parser.add_argument("--interval", type=float, default=60.0)
    daemon_parser.add_argument("--cycles", type=int)

    webhook_parser = subparsers.add_parser("webhook", help="Ingest a GitHub webhook payload")
    webhook_parser.add_argument("--event-type", default="")
    webhook_parser.add_argument("--payload-file")

    queue_parser = subparsers.add_parser("queue", help="Inspect the local queue")
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command", required=True)
    list_parser = queue_subparsers.add_parser("list", help="List pending events")
    _add_queue_list_args(list_parser)
    peek_parser = queue_subparsers.add_parser("peek", help="Peek pending events")
    peek_parser.add_argument("--limit", type=int, default=5)
    dead_parser = queue_subparsers.add_parser("dead", help="List failed events")
    _add_queue_list_args(dead_parser)
    done_parser = queue_subparsers.add_parser("done", help="List completed events")
    _add_queue_list_args(done_parser)
    retry_parser = queue_subparsers.add_parser("retry", help="Move failed events back to pending")
    retry_target = retry_parser.add_mutually_exclusive_group(required=True)
    retry_target.add_argument("--event-id")
    retry_target.add_argument("--all", action="store_true")
    retry_parser.add_argument("--limit", type=int)
    replay_parser = queue_subparsers.add_parser("replay", help="Move completed events back to pending")
    replay_target = replay_parser.add_mutually_exclusive_group(required=True)
    replay_target.add_argument("--event-id")
    replay_target.add_argument("--all", action="store_true")
    replay_parser.add_argument("--limit", type=int)

    return parser


def _app_from_args(args: Any) -> GitHubPMAgentApp:
    config = load_config(args.config)
    return GitHubPMAgentApp(config, project_root(config))


def _load_payload(path: str | None) -> Any:
    if path:
        payload_path = Path(path).expanduser().resolve()
        return json.loads(payload_path.read_text(encoding="utf-8"))
    return json.loads(sys.stdin.read() or "{}")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # Handle `release` before building a full app — uses config directly.
    if args.command == "release":
        config = load_config(args.config)
        root = project_root(config)
        from github_pm_agent.project_release import ProjectRelease
        releaser = ProjectRelease(config, root)
        result = releaser.release()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    # Handle `start` before building a full app — the template config has no github.repo yet.
    if args.command == "start":
        requirements_path = Path(args.requirements).expanduser().resolve()
        if not requirements_path.exists():
            print(f"Error: requirements file not found: {requirements_path}", file=sys.stderr)
            return 1
        requirements = requirements_path.read_text(encoding="utf-8")
        config = load_config(args.config)
        root = project_root(config)
        from github_pm_agent.project_initializer import ProjectInitializer
        initializer = ProjectInitializer(config, root)
        result = initializer.initialize(requirements)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    app = _app_from_args(args)

    if args.command == "poll":
        print(json.dumps(app.poll(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "cycle":
        print(json.dumps(app.cycle(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "reconcile":
        print(json.dumps(app.reconcile(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "analytics":
        print(json.dumps(app.analytics(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "daemon":
        print(
            json.dumps(
                app.daemon(interval_seconds=args.interval, max_cycles=args.cycles),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "webhook":
        payload = _load_payload(getattr(args, "payload_file", None))
        print(
            json.dumps(
                app.ingest_webhook(payload, event_type=getattr(args, "event_type", "")),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "queue":
        queue = QueueStore(runtime_dir(app.config))
        if args.queue_command == "list":
            payload = [
                event.to_dict()
                for event in queue.list_pending(limit=args.limit, event_id=args.event_id)
            ]
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if args.queue_command == "peek":
            payload = [event.to_dict() for event in queue.peek(limit=args.limit)]
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if args.queue_command == "dead":
            print(
                json.dumps(
                    queue.list_dead(limit=args.limit, event_id=args.event_id),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if args.queue_command == "done":
            print(
                json.dumps(
                    queue.list_done(limit=args.limit, event_id=args.event_id),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if args.queue_command == "retry":
            payload = queue.retry_dead(
                event_id=args.event_id,
                limit=args.limit,
            )
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if args.queue_command == "replay":
            payload = queue.replay_done(
                event_id=args.event_id,
                limit=args.limit,
            )
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

    parser.error("unknown command")
    return 1
