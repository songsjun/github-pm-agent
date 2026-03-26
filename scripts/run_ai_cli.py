#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local AI CLI and normalize output as JSON.")
    parser.add_argument("--provider", required=True, choices=["codex", "gemini"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--input-file", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--session-key", default="")
    parser.add_argument("--schema-file", default="")
    parser.add_argument("--codex-path", default="/opt/homebrew/bin/codex")
    parser.add_argument("--gemini-path", default="/opt/homebrew/bin/gemini")
    parser.add_argument("--reasoning-effort", default="", help="low/medium/high/xhigh")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    return parser


def run_codex(args: argparse.Namespace, prompt: str) -> Dict[str, Any]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        output_file = handle.name
    command: List[str] = [
        args.codex_path,
        "exec",
        "--sandbox",
        "read-only",
        "-c",
        'approval_policy="never"',
        "--output-last-message",
        output_file,
        "--skip-git-repo-check",
    ]
    if args.model:
        command.extend(["--model", args.model])
    if getattr(args, "reasoning_effort", ""):
        command.extend(["-c", f'model_reasoning_effort="{args.reasoning_effort}"'])
    if args.schema_file:
        command.extend(["--output-schema", args.schema_file])
    command.append("-")
    result = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        cwd=args.cwd,
        check=False,
        timeout=args.timeout_seconds,
    )
    output = Path(output_file).read_text(encoding="utf-8").strip() if Path(output_file).exists() else ""
    if result.returncode != 0:
        raise RuntimeError(
            f"codex cli failed with exit code {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return {
        "provider": "codex_cli",
        "model": args.model,
        "session_key": args.session_key or None,
        "output": output,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def run_gemini(args: argparse.Namespace, prompt: str) -> Dict[str, Any]:
    command: List[str] = [
        args.gemini_path,
        "--approval-mode",
        "plan",
        "--output-format",
        "json",
        "-m",
        args.model,
        "-p",
        prompt,
    ]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        cwd=args.cwd,
        check=False,
        timeout=args.timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gemini cli failed with exit code {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gemini cli did not return valid JSON: {exc}") from exc
    return {
        "provider": "gemini_cli",
        "model": args.model,
        "session_key": payload.get("session_id") or args.session_key or None,
        "output": payload.get("response", ""),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "stats": payload.get("stats", {}),
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    prompt = Path(args.input_file).read_text(encoding="utf-8")
    try:
        if args.provider == "codex":
            payload = run_codex(args, prompt)
        else:
            payload = run_gemini(args, prompt)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{args.provider} cli timed out after {args.timeout_seconds}s"
        ) from exc
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
