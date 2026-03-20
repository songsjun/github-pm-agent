from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import yaml

from github_pm_agent.utils import ensure_dir


class ConfigError(RuntimeError):
    pass


def load_config(config_path: str) -> Dict[str, Any]:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        config = yaml.safe_load(raw_text) or {}
    else:
        config = json.loads(raw_text)
    if not isinstance(config, dict):
        raise ConfigError("config root must be an object")
    config["_config_path"] = str(path)
    config["_project_root"] = str(path.parent.parent if path.parent.name == "config" else path.parent)
    return config


def project_root(config: Dict[str, Any]) -> Path:
    return Path(config["_project_root"]).resolve()


def runtime_dir(config: Dict[str, Any]) -> Path:
    root = project_root(config)
    runtime_path = config.get("runtime_dir") or config.get("runtime", {}).get("state_dir", "runtime")
    runtime = root / runtime_path
    ensure_dir(runtime)
    ensure_dir(runtime / "sessions")
    return runtime


def repo_name(config: Dict[str, Any]) -> str:
    github = config.get("github", {})
    repo = github.get("repo")
    if repo:
        return repo

    repos = github.get("repos")
    if isinstance(repos, str) and repos:
        return repos
    if isinstance(repos, (list, tuple)) and repos:
        return str(repos[0])

    raise ConfigError("github.repo or github.repos[0] is required")


def gh_path(config: Dict[str, Any]) -> str:
    return config.get("github", {}).get("gh_path", "gh")
