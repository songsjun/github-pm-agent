from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso8601(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[Any]:
    if not path.exists():
        return []
    items: List[Any] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def write_jsonl(path: Path, items: Iterable[Any]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def load_text(path: Optional[Path]) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def extract_json_object(text: str) -> Optional[Any]:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, flags=re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
            return payload
        except json.JSONDecodeError:
            continue
    return None


def git_auth_env(
    github_token: str,
    *,
    host: str = "github.com",
    base_env: Optional[dict[str, str]] = None,
) -> Optional[dict[str, str]]:
    """Return subprocess env that authenticates git over HTTPS without persisting the token."""
    if not github_token:
        return dict(base_env) if base_env is not None else None

    env = dict(base_env) if base_env is not None else dict(os.environ)
    basic_auth = base64.b64encode(f"x-access-token:{github_token}".encode("utf-8")).decode("ascii")

    try:
        config_count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        config_count = 0

    env["GIT_CONFIG_COUNT"] = str(config_count + 1)
    env[f"GIT_CONFIG_KEY_{config_count}"] = f"http.https://{host}/.extraheader"
    env[f"GIT_CONFIG_VALUE_{config_count}"] = f"AUTHORIZATION: basic {basic_auth}"
    return env
