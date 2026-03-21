from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

from github_pm_agent.utils import ensure_dir


class ConfigError(RuntimeError):
    pass


def load_config(config_path: str) -> Dict[str, Any]:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"} and _yaml is not None:
        config = _yaml.safe_load(raw_text) or {}
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
    # Support flat `runtime_dir` key as an alias for `runtime.state_dir`
    flat = config.get("runtime_dir")
    if flat:
        state_dir = str(flat)
    else:
        state_dir = config.get("runtime", {}).get("state_dir", "runtime")
    runtime = root / state_dir
    ensure_dir(runtime)
    ensure_dir(runtime / "sessions")
    return runtime


def repo_name(config: Dict[str, Any]) -> str:
    repos = repo_names(config)
    return repos[0]


def repo_names(config: Dict[str, Any]) -> List[str]:
    github = config.get("github", {})
    repos = github.get("repos")
    if isinstance(repos, list) and repos:
        normalized = [str(repo).strip() for repo in repos if str(repo).strip()]
        if normalized:
            return normalized
    repo = github.get("repo")
    if not repo:
        raise ConfigError("github.repo is required")
    return [str(repo).strip()]


def gh_path(config: Dict[str, Any]) -> str:
    return config.get("github", {}).get("gh_path", "gh")
