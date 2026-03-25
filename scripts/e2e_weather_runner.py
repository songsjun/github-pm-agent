#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "e2e-weather-live.json"
VENV_PATH = PROJECT_ROOT / ".venv-e2e"
RUNTIME_DIR = PROJECT_ROOT / ".runtime" / "e2e-weather"
LOG_DIR = RUNTIME_DIR / "logs"
STATE_DIR = RUNTIME_DIR / "local-devenv"
SERVER_PID_PATH = RUNTIME_DIR / "local-devenv.pid"
DAEMON_PID_PATH = RUNTIME_DIR / "agent-daemon.pid"
CURRENT_RUN_PATH = RUNTIME_DIR / "current_run.json"
REQUIREMENTS_DIR = RUNTIME_DIR / "requirements"

PM_LOGIN = "songsjun"
CUSTOMER_LOGIN = "sjunsong"
WORKERS = ["kapy9250", "otter9527"]
SERVER_URL = "http://127.0.0.1:17070"
DISCUSSION_CATEGORY_PREFERENCE = ["Ideas", "General", "Q&A", "Announcements"]
LABEL_SPECS = [
    ("ready-to-code", "0e8a16", "Ready for automated coding"),
    ("workflow-gate", "fbca04", "Human confirmation required"),
    ("enhancement", "a2eeef", "New feature or request"),
    ("bug", "d73a4a", "Something is not working"),
    ("documentation", "0075ca", "Documentation work"),
    ("frontend", "1d76db", "Frontend implementation"),
    ("backend", "5319e7", "Backend implementation"),
    ("infrastructure", "6f42c1", "Infrastructure or tooling"),
    ("content", "bf3989", "Content or domain data"),
    ("test", "0f8b8d", "Automated test work"),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full Weather Atlas workflow e2e.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Generate requirements, create repo/discussion, and start the agent.")
    start.add_argument("--restart", action="store_true", help="Stop local services and reset local runtime state first.")

    sub.add_parser("status", help="Show the current e2e run status.")

    confirm = sub.add_parser("confirm", help="Post a customer confirmation on the active gate.")
    confirm.add_argument("--body", default="approve", help="Comment body to post as the customer.")

    sub.add_parser("stop", help="Stop local background services for the e2e run.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "start":
        return start_run(restart=args.restart)
    if args.command == "status":
        return show_status()
    if args.command == "confirm":
        return confirm_gate(args.body)
    if args.command == "stop":
        return stop_services()
    parser.error(f"unknown command: {args.command}")
    return 2


def start_run(*, restart: bool) -> int:
    tokens = resolve_tokens()
    if restart:
        stop_pid_file(DAEMON_PID_PATH)
        stop_pid_file(SERVER_PID_PATH)
        if RUNTIME_DIR.exists():
            shutil.rmtree(RUNTIME_DIR)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REQUIREMENTS_DIR.mkdir(parents=True, exist_ok=True)

    ensure_venv()
    start_local_devenv()

    requirements = write_requirements_markdown()
    repo = f"{PM_LOGIN}/{build_repo_name(requirements['product_name'])}"
    ensure_repo_and_access(repo, tokens)
    default_branch = repo_metadata(repo, tokens["pm"]).get("default_branch") or "main"
    config_path = write_runtime_config(repo, default_branch)
    discussion = create_seed_discussion(repo, requirements, tokens["pm"])

    run_info = {
        "repo": repo,
        "default_branch": default_branch,
        "customer": CUSTOMER_LOGIN,
        "pm": PM_LOGIN,
        "workers": WORKERS,
        "requirement_path": requirements["path"],
        "discussion_number": discussion["number"],
        "discussion_id": discussion["id"],
        "discussion_url": discussion["url"],
        "discussion_title": discussion["title"],
        "config_path": str(config_path),
        "started_at": utc_now_iso(),
    }
    CURRENT_RUN_PATH.write_text(json.dumps(run_info, indent=2), encoding="utf-8")

    cycle_result = run_agent_cli(["cycle"], env=agent_env(tokens), config_path=config_path, capture_json=True)
    start_daemon(tokens, config_path)

    print(
        json.dumps(
            {
                "repo": repo,
                "requirement_path": requirements["path"],
                "discussion_number": discussion["number"],
                "discussion_url": discussion["url"],
                "config_path": str(config_path),
                "cycle": cycle_result,
                "logs": {
                    "agent": str(LOG_DIR / "agent-daemon.log"),
                    "devenv": str(LOG_DIR / "local-devenv.log"),
                },
                "next_steps": [
                    "python3 scripts/e2e_weather_runner.py status",
                    "python3 scripts/e2e_weather_runner.py confirm --body approve",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def show_status() -> int:
    if not CURRENT_RUN_PATH.exists():
        print(json.dumps({"status": "no_active_run", "runtime_dir": str(RUNTIME_DIR)}, ensure_ascii=False, indent=2))
        return 0

    run_info = json.loads(CURRENT_RUN_PATH.read_text(encoding="utf-8"))
    repo = str(run_info["repo"])
    pm_token = gh_token_for(PM_LOGIN)
    workflow_states = load_workflow_states(repo)
    pending_gates = detect_pending_gates(workflow_states)
    discussion = get_discussion(repo, int(run_info["discussion_number"]), pm_token)
    prs = list_pull_requests(repo, pm_token)
    issue_prs = prs_by_closing_issue(prs)
    issues = list_repo_issues(repo, pm_token)

    payload = {
        "repo": repo,
        "requirement_path": run_info.get("requirement_path", ""),
        "server_running": pid_is_running(read_pid(SERVER_PID_PATH)),
        "daemon_running": pid_is_running(read_pid(DAEMON_PID_PATH)),
        "discussion": {
            "number": run_info["discussion_number"],
            "url": run_info["discussion_url"],
            "title": discussion.get("title") or run_info.get("discussion_title", ""),
            "latest_comment": latest_discussion_comment(discussion),
        },
        "pending_gates": pending_gates,
        "workflows": workflow_states,
        "issues": [
            summarize_issue(issue, workflow_states, issue_prs.get(issue["number"]))
            for issue in issues
        ],
        "pull_requests": prs,
        "logs": {
            "agent": str(LOG_DIR / "agent-daemon.log"),
            "devenv": str(LOG_DIR / "local-devenv.log"),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def confirm_gate(body: str) -> int:
    if not CURRENT_RUN_PATH.exists():
        raise SystemExit("no current run found")

    run_info = json.loads(CURRENT_RUN_PATH.read_text(encoding="utf-8"))
    repo = str(run_info["repo"])
    pending_gates = detect_pending_gates(load_workflow_states(repo))
    if not pending_gates:
        raise SystemExit("no active gate found")

    gate = pending_gates[0]
    customer_token = gh_token_for(CUSTOMER_LOGIN)
    if gate["target_kind"] == "discussion":
        discussion = get_discussion(repo, int(gate["target_number"]), customer_token)
        discussion_id = discussion.get("id") or run_info.get("discussion_id")
        if not discussion_id:
            raise SystemExit("unable to resolve discussion id for gate")
        add_discussion_comment(str(discussion_id), body, customer_token)
    else:
        run_gh(
            ["issue", "comment", str(gate["target_number"]), "--repo", repo, "--body", body],
            token=customer_token,
        )

    print(
        json.dumps(
            {
                "repo": repo,
                "target_kind": gate["target_kind"],
                "target_number": gate["target_number"],
                "phase": gate["phase"],
                "comment": body,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def stop_services() -> int:
    stop_pid_file(DAEMON_PID_PATH)
    stop_pid_file(SERVER_PID_PATH)
    print(
        json.dumps(
            {
                "stopped": True,
                "server_pid": read_pid(SERVER_PID_PATH),
                "daemon_pid": read_pid(DAEMON_PID_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def write_requirements_markdown() -> dict[str, str]:
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    product_name = "Weather Atlas"
    path = REQUIREMENTS_DIR / f"weather-atlas-{timestamp}.md"
    content = (
        f"# {product_name}\n\n"
        f"customer: {CUSTOMER_LOGIN}\n"
        f"pm: {PM_LOGIN}\n\n"
        "## Goal\n"
        "Build a production-shaped MVP website that shows real-time weather forecasts for places around the world.\n\n"
        "## Functional Requirements\n"
        "- Show weather data using the free Open-Meteo APIs. Do not require an API key.\n"
        "- Support geographic scope switching: Global, Country, Region/State, City.\n"
        "- Support switching between List view and Map view.\n"
        "- Let the user search for a city and inspect the selected place, current weather, and a short daily forecast summary.\n"
        "- Keep the implementation frontend-first and simple to run locally.\n\n"
        "## Product Expectations\n"
        "- The UX should feel deliberate, not boilerplate.\n"
        "- The MVP should work well on desktop and mobile.\n"
        "- Break work into concrete implementation issues with acceptance tests.\n\n"
        "## Delivery Constraints\n"
        "- Use GitHub Discussions for product discovery and design review before coding.\n"
        "- Use GitHub Issues and PRs for implementation.\n"
        "- Customer approvals should come from @sjunsong.\n"
    )
    path.write_text(content, encoding="utf-8")
    return {
        "product_name": product_name,
        "path": str(path),
        "body": content,
    }


def build_repo_name(product_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", product_name.lower()).strip("-")
    timestamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    return f"{slug}-flow-{timestamp}"


def ensure_venv() -> None:
    if not VENV_PATH.exists():
        run_local(["python3", "-m", "venv", str(VENV_PATH)])
    run_local([str(VENV_PATH / "bin" / "python"), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run_local([str(VENV_PATH / "bin" / "python"), "-m", "pip", "install", "--quiet", "-e", str(PROJECT_ROOT)])


def start_local_devenv() -> None:
    if healthcheck(SERVER_URL):
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "local-devenv.log"
    process = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "local_devenv_server.py"),
            "--host",
            "127.0.0.1",
            "--port",
            "17070",
            "--state-dir",
            str(STATE_DIR),
        ],
        cwd=str(PROJECT_ROOT),
        stdout=log_path.open("a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
        start_new_session=True,
    )
    SERVER_PID_PATH.write_text(str(process.pid), encoding="utf-8")
    deadline = time.time() + 20
    while time.time() < deadline:
        if healthcheck(SERVER_URL):
            return
        if process.poll() is not None:
            raise RuntimeError(f"local DevEnv server exited early; see {log_path}")
        time.sleep(0.5)
    raise RuntimeError(f"local DevEnv server did not become healthy; see {log_path}")


def ensure_repo_and_access(repo: str, tokens: dict[str, str]) -> None:
    pm_token = tokens["pm"]
    if not repo_exists(repo, pm_token):
        run_gh(
            ["repo", "create", repo, "--private", "--add-readme", "--confirm"],
            token=pm_token,
        )
    enable_discussions(repo, pm_token)
    for login in [CUSTOMER_LOGIN, *WORKERS]:
        run_gh(
            ["api", "-X", "PUT", f"repos/{repo}/collaborators/{login}", "-f", "permission=push"],
            token=pm_token,
        )
    for login, token in [
        (CUSTOMER_LOGIN, tokens["customer"]),
        (WORKERS[0], tokens["kapy"]),
        (WORKERS[1], tokens["otter"]),
    ]:
        accept_repo_invitation(repo, token)
    for label, color, desc in LABEL_SPECS:
        run_gh(
            ["label", "create", label, "--repo", repo, "--color", color, "--description", desc, "--force"],
            token=pm_token,
        )


def enable_discussions(repo: str, token: str) -> None:
    run_gh(["api", "-X", "PATCH", f"repos/{repo}", "-F", "has_discussions=true"], token=token)
    deadline = time.time() + 20
    while time.time() < deadline:
        context = discussion_repository_context(repo, token)
        if context.get("category_id"):
            return
        time.sleep(1)
    raise RuntimeError(f"discussion categories did not become available for {repo}")


def repo_metadata(repo: str, token: str) -> dict[str, Any]:
    payload = gh_json(["api", f"repos/{repo}"], token=token)
    return payload if isinstance(payload, dict) else {}


def write_runtime_config(repo: str, default_branch: str) -> Path:
    config = {
        "github": {
            "repos": [repo],
            "default_branch": default_branch,
            "owner": CUSTOMER_LOGIN,
            "gh_path": "gh",
            "mentions": [f"@{CUSTOMER_LOGIN}", f"@{PM_LOGIN}", *[f"@{login}" for login in WORKERS]],
        },
        "agents": [
            {
                "id": "pm",
                "role": "pm",
                "login": PM_LOGIN,
                "token_env": "GITHUB_TOKEN_PM",
                "priority": 1,
                "participates_in": {
                    "pull_request_changed": "observe",
                },
            },
            {
                "id": "kapy",
                "role": "worker",
                "login": WORKERS[0],
                "worker_index": 1,
                "token_env": "GITHUB_TOKEN_KAPY",
            },
            {
                "id": "otter",
                "role": "worker",
                "login": WORKERS[1],
                "worker_index": 2,
                "token_env": "GITHUB_TOKEN_OTTER",
            },
        ],
        "engine": {
            "dry_run": False,
            "continue_on_error": True,
            "supervisor_enabled": False,
        },
        "ai": {
            "default_provider": "codex_cli",
            "default_model": "gpt-5.4",
            "providers": {
                "codex_cli": {
                    "type": "cli_script",
                    "provider_name": "codex",
                    "script": "scripts/run_ai_cli.py",
                    "python_path": "python3",
                    "codex_path": "codex",
                    "default_model": "gpt-5.4",
                    "reasoning_effort": "medium",
                }
            },
        },
        "devenv": {
            "server_url": SERVER_URL,
            "base_image": "node:20-slim",
        },
        "runtime_dir": ".runtime/e2e-weather",
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return CONFIG_PATH


def create_seed_discussion(repo: str, requirements: dict[str, str], token: str) -> dict[str, Any]:
    context = discussion_repository_context(repo, token)
    mutation = """
    mutation($repositoryId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
      createDiscussion(input: {repositoryId: $repositoryId, categoryId: $categoryId, title: $title, body: $body}) {
        discussion { id number url title }
      }
    }
    """
    payload = gh_graphql(
        mutation,
        {
            "repositoryId": context["repository_id"],
            "categoryId": context["category_id"],
            "title": f"Weather Atlas MVP - product discovery ({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())})",
            "body": requirements["body"],
        },
        token=token,
    )
    discussion = (((payload.get("data") or {}).get("createDiscussion") or {}).get("discussion") or {})
    if not discussion:
        raise RuntimeError(f"failed to create discussion in {repo}: {payload}")
    return discussion


def discussion_repository_context(repo: str, token: str) -> dict[str, str]:
    owner, name = repo.split("/", 1)
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        id
        discussionCategories(first: 20) {
          nodes {
            id
            name
          }
        }
      }
    }
    """
    payload = gh_graphql(query, {"owner": owner, "name": name}, token=token)
    repository = ((payload.get("data") or {}).get("repository") or {})
    categories = ((repository.get("discussionCategories") or {}).get("nodes") or [])
    category_id = ""
    categories_by_name = {str(item.get("name", "")): str(item.get("id", "")) for item in categories if item.get("id")}
    for preferred in DISCUSSION_CATEGORY_PREFERENCE:
        if categories_by_name.get(preferred):
            category_id = categories_by_name[preferred]
            break
    if not category_id and categories:
        category_id = str(categories[0].get("id") or "")
    return {
        "repository_id": str(repository.get("id") or ""),
        "category_id": category_id,
    }


def get_discussion(repo: str, number: int, token: str) -> dict[str, Any]:
    owner, name = repo.split("/", 1)
    query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        discussion(number: $number) {
          id
          number
          title
          url
          body
          updatedAt
          comments(last: 20) {
            nodes {
              createdAt
              body
              author { login }
            }
          }
        }
      }
    }
    """
    payload = gh_graphql(query, {"owner": owner, "name": name, "number": number}, token=token)
    discussion = ((payload.get("data") or {}).get("repository") or {}).get("discussion") or {}
    return discussion if isinstance(discussion, dict) else {}


def add_discussion_comment(discussion_id: str, body: str, token: str) -> None:
    mutation = """
    mutation($discussionId: ID!, $body: String!) {
      addDiscussionComment(input: {discussionId: $discussionId, body: $body}) {
        comment { id }
      }
    }
    """
    gh_graphql(mutation, {"discussionId": discussion_id, "body": body}, token=token)


def load_workflow_states(repo: str) -> list[dict[str, Any]]:
    safe_repo = repo.replace("/", "__", 1)
    root = RUNTIME_DIR / "workflows" / safe_repo
    states: list[dict[str, Any]] = []
    if not root.exists():
        return states
    for state_path in sorted(root.glob("*/state.json"), key=lambda path: int(path.parent.name)):
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        states.append(
            {
                "number": int(state_path.parent.name),
                "workflow_type": raw.get("workflow_type") or ((raw.get("original_event") or {}).get("event_type") or ""),
                "phase": raw.get("phase"),
                "completed": bool(raw.get("completed")),
                "terminated": bool(raw.get("terminated")),
                "terminated_reason": raw.get("terminated_reason", ""),
                "gate_issue_number": raw.get("gate_issue_number"),
                "gate_next_phase": raw.get("gate_next_phase"),
                "gate_resume_mode": raw.get("gate_resume_mode"),
                "gate_discussion_node_id": raw.get("gate_discussion_node_id"),
                "clarification": (
                    {
                        "phase": raw.get("clarification_phase"),
                        "posted_at": raw.get("clarification_posted_at"),
                        "node_id": raw.get("clarification_node_id", ""),
                    }
                    if raw.get("clarification_phase")
                    else None
                ),
                "artifacts": sorted((raw.get("artifacts") or {}).keys()),
                "pr_number": (raw.get("artifacts") or {}).get("pr_number"),
                "pr_url": (raw.get("artifacts") or {}).get("pr_url"),
            }
        )
    return states


def detect_pending_gates(workflow_states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for state in workflow_states:
        if state["completed"] or state["terminated"]:
            continue
        if state.get("clarification"):
            gates.append(
                {
                    "workflow_type": state["workflow_type"],
                    "phase": state["phase"],
                    "target_kind": "discussion",
                    "target_number": state["number"],
                    "reason": "clarification",
                }
            )
            continue
        if state.get("gate_discussion_node_id") and state.get("gate_next_phase"):
            gates.append(
                {
                    "workflow_type": state["workflow_type"],
                    "phase": state["phase"],
                    "target_kind": "discussion",
                    "target_number": state["number"],
                    "reason": "phase_gate",
                }
            )
            continue
        gate_issue_number = state.get("gate_issue_number")
        if gate_issue_number:
            gates.append(
                {
                    "workflow_type": state["workflow_type"],
                    "phase": state["phase"],
                    "target_kind": "issue",
                    "target_number": gate_issue_number,
                    "reason": "phase_gate",
                }
            )
    return sorted(gates, key=lambda item: (0 if item["target_kind"] == "discussion" else 1, int(item["target_number"])))


def list_repo_issues(repo: str, token: str) -> list[dict[str, Any]]:
    payload = gh_json(["api", f"repos/{repo}/issues?state=all&per_page=100"], token=token)
    issues = payload if isinstance(payload, list) else []
    result = []
    for issue in issues:
        if issue.get("pull_request"):
            continue
        result.append(
            {
                "number": issue["number"],
                "title": issue.get("title", ""),
                "state": issue.get("state", ""),
                "labels": [str((label or {}).get("name", "")) for label in issue.get("labels", []) if (label or {}).get("name")],
                "html_url": issue.get("html_url", ""),
            }
        )
    return sorted(result, key=lambda item: int(item["number"]))


def list_pull_requests(repo: str, token: str) -> list[dict[str, Any]]:
    payload = gh_json(
        ["pr", "list", "--repo", repo, "--state", "all", "--json", "number,url,title,body,state,headRefName,author"],
        token=token,
    )
    prs = payload if isinstance(payload, list) else []
    return sorted(prs, key=lambda item: int(item.get("number", 0)))


def prs_by_closing_issue(prs: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    pattern = re.compile(r"closes\s+#(\d+)", re.IGNORECASE)
    for pr in prs:
        body = str(pr.get("body") or "")
        match = pattern.search(body)
        if match:
            result[int(match.group(1))] = pr
    return result


def summarize_issue(issue: dict[str, Any], workflow_states: list[dict[str, Any]], pr: dict[str, Any] | None) -> dict[str, Any]:
    state = next((item for item in workflow_states if int(item["number"]) == int(issue["number"])), None)
    return {
        "number": issue["number"],
        "title": issue["title"],
        "state": issue["state"],
        "labels": issue["labels"],
        "url": issue["html_url"],
        "workflow_type": (state or {}).get("workflow_type", ""),
        "phase": (state or {}).get("phase", ""),
        "completed": bool((state or {}).get("completed")),
        "terminated": bool((state or {}).get("terminated")),
        "pr": pr or {},
    }


def latest_discussion_comment(discussion: dict[str, Any]) -> str:
    nodes = ((discussion.get("comments") or {}).get("nodes") or []) if isinstance(discussion, dict) else []
    if not nodes:
        return ""
    latest = nodes[-1]
    author = ((latest.get("author") or {}).get("login") or "unknown")
    body = str(latest.get("body") or "").strip()
    return f"@{author}: {body[:300]}"


def accept_repo_invitation(repo: str, token: str) -> None:
    invitations = gh_json(["api", "user/repository_invitations"], token=token)
    for invitation in invitations if isinstance(invitations, list) else []:
        invited_repo = ((invitation or {}).get("repository") or {}).get("full_name")
        invite_id = invitation.get("id")
        if invited_repo == repo and invite_id:
            run_gh(["api", "-X", "PATCH", f"user/repository_invitations/{invite_id}"], token=token)


def repo_exists(repo: str, token: str) -> bool:
    result = subprocess.run(
        ["gh", "repo", "view", repo],
        cwd=str(PROJECT_ROOT),
        env=gh_env(token),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
    )
    return result.returncode == 0


def resolve_tokens() -> dict[str, str]:
    return {
        "pm": gh_token_for(PM_LOGIN),
        "customer": gh_token_for(CUSTOMER_LOGIN),
        "kapy": gh_token_for(WORKERS[0]),
        "otter": gh_token_for(WORKERS[1]),
    }


def gh_token_for(login: str) -> str:
    result = subprocess.run(
        ["gh", "auth", "token", "--user", login],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"unable to resolve gh token for {login}: {result.stderr.strip() or result.stdout.strip()}")
    token = result.stdout.strip()
    if not token:
        raise RuntimeError(f"empty gh token for {login}")
    return token


def run_agent_cli(args: list[str], *, env: dict[str, str], config_path: Path, capture_json: bool = False) -> Any:
    command = [str(VENV_PATH / "bin" / "github-pm-agent"), "--config", str(config_path), *args]
    result = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "github-pm-agent command failed")
    if not capture_json:
        return result.stdout.strip()
    return json.loads(result.stdout)


def start_daemon(tokens: dict[str, str], config_path: Path) -> None:
    stop_pid_file(DAEMON_PID_PATH)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "agent-daemon.log"
    process = subprocess.Popen(
        [
            str(VENV_PATH / "bin" / "github-pm-agent"),
            "--config",
            str(config_path),
            "daemon",
            "--interval",
            "10",
        ],
        cwd=str(PROJECT_ROOT),
        stdout=log_path.open("a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        env=agent_env(tokens),
        start_new_session=True,
    )
    DAEMON_PID_PATH.write_text(str(process.pid), encoding="utf-8")


def gh_graphql(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if isinstance(value, bool):
            args.extend(["-F", f"{key}={'true' if value else 'false'}"])
        elif isinstance(value, int):
            args.extend(["-F", f"{key}={value}"])
        else:
            args.extend(["-f", f"{key}={value}"])
    payload = gh_json(args, token=token)
    return payload if isinstance(payload, dict) else {}


def run_gh(args: list[str], *, token: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        cwd=str(PROJECT_ROOT),
        env=gh_env(token),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"gh {' '.join(args)} failed")
    return result.stdout.strip()


def gh_json(args: list[str], *, token: str) -> Any:
    output = run_gh(args, token=token)
    return json.loads(output) if output else {}


def gh_env(token: str) -> dict[str, str]:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    return env


def agent_env(tokens: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env["DEVENV_SERVER"] = SERVER_URL
    env["GITHUB_TOKEN_PM"] = tokens["pm"]
    env["GITHUB_TOKEN_KAPY"] = tokens["kapy"]
    env["GITHUB_TOKEN_OTTER"] = tokens["otter"]
    return env


def run_local(command: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(
        command,
        cwd=str(cwd or PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {' '.join(command)}")


def healthcheck(url: str) -> bool:
    try:
        with urlopen(f"{url}/health", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("status") == "ok"
    except Exception:
        return False


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def pid_is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop_pid_file(path: Path) -> None:
    pid = read_pid(path)
    if pid and pid_is_running(pid):
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 10
        while time.time() < deadline and pid_is_running(pid):
            time.sleep(0.2)
        if pid_is_running(pid):
            os.kill(pid, signal.SIGKILL)
    if path.exists():
        path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
