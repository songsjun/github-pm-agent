from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from github_pm_agent.actions import GitHubActionToolkit
from github_pm_agent.ai_adapter import AIAdapterManager
from github_pm_agent.config import gh_path, repo_name, runtime_dir
from github_pm_agent.engine import EventEngine
from github_pm_agent.github_client import GitHubClient
from github_pm_agent.poller import GitHubPoller
from github_pm_agent.prompt_library import PromptLibrary
from github_pm_agent.queue_store import QueueStore
from github_pm_agent.role_registry import RoleRegistry
from github_pm_agent.session_store import SessionStore
from github_pm_agent.status_probe import StatusProbe
from github_pm_agent.utils import read_json, utc_now_iso, write_json
from github_pm_agent.phase_gate_scanner import PhaseGateScanner
from github_pm_agent.workflow_orchestrator import WorkflowOrchestrator


class GitHubPMAgentApp:
    def __init__(self, config: Dict[str, Any], project_root: Path) -> None:
        self.config = config
        self.project_root = project_root
        self.runtime_dir = runtime_dir(config)
        # Use the lowest-priority (primary) agent's token for the shared client
        _primary_token_env = next(
            (a.get("token_env") for a in sorted(config.get("agents", []), key=lambda a: a.get("priority", 99)) if a.get("token_env")),
            None,
        )
        self.client = GitHubClient(gh_path(config), repo_name(config), token_env=_primary_token_env)
        self.queue = QueueStore(self.runtime_dir)
        self.prompts = PromptLibrary(project_root)
        self.sessions = SessionStore(self.runtime_dir)
        self.ai = AIAdapterManager(project_root, config, self.prompts, self.sessions)
        self.actions = GitHubActionToolkit(
            self.client,
            self.runtime_dir,
            dry_run=config.get("engine", {}).get("dry_run", True),
        )
        self.engine = EventEngine(config, self.ai, self.actions, self.runtime_dir)
        self.engine.role_registry = RoleRegistry(project_root)
        agent_configs = config.get("agents", [])
        dry_run = config.get("engine", {}).get("dry_run", True)
        _gh_path = gh_path(config)
        _primary_repo = repo_name(config)
        agent_toolkits: Dict[str, Any] = {}
        for agent_cfg in agent_configs:
            token_env = agent_cfg.get("token_env")
            agent_client = GitHubClient(_gh_path, _primary_repo, token_env=token_env)
            agent_toolkits[agent_cfg["id"]] = GitHubActionToolkit(
                agent_client, self.runtime_dir, dry_run=dry_run
            )
        self.orchestrator = WorkflowOrchestrator(
            project_root,
            self.engine,
            self.actions,
            self.client,
            config,
            agent_configs=agent_configs,
            agent_toolkits=agent_toolkits,
        )
        from github_pm_agent.queue_store import SuspendedEventScanner

        owner_login = config.get("github", {}).get("owner", "")
        self.scanner = SuspendedEventScanner(self.queue, self.client, owner_login)
        self.gate_scanner = PhaseGateScanner(self.queue, self.client, owner_login)
        self.cursors_path = self.runtime_dir / "cursors.json"

    def poll(self) -> Dict[str, Any]:
        cursor = read_json(self.cursors_path, {"since": "1970-01-01T00:00:00Z"})
        since = cursor.get("since", "1970-01-01T00:00:00Z")
        poller = GitHubPoller(
            self.client,
            repo_name(self.config),
            self.config.get("github", {}).get("default_branch", "main"),
            self.config.get("github", {}).get("mentions", []),
        )
        events = poller.poll(since)
        probe = StatusProbe(self.client, repo_name(self.config), self.config)
        synthetic_events = probe.scan()
        enqueued = self.queue.enqueue(events + synthetic_events)
        write_json(self.cursors_path, {"since": utc_now_iso()})
        return {
            "since": since,
            "events_found": len(events),
            "synthetic_events_found": len(synthetic_events),
            "events_enqueued": enqueued,
        }

    def cycle(self) -> Dict[str, Any]:
        poll_result = self.poll()
        resume_result = self.scanner.scan_and_resume()
        gate_advance_result = self.gate_scanner.scan_and_advance()
        processed: List[Dict[str, Any]] = []
        while True:
            event = self.queue.pop()
            if event is None:
                break
            try:
                result = self.orchestrator.process(event)
                escalation_refs = result.get("escalation_refs", [])
                if escalation_refs:
                    for ref in escalation_refs:
                        self.queue.mark_suspended(
                            event,
                            ref["issue_number"],
                            ref["key"],
                            ref["reason_class"],
                        )
                else:
                    self.queue.mark_done(event, result)
                processed.append({"event_id": event.event_id, "result": result})
            except Exception as exc:  # noqa: BLE001
                self.queue.mark_failed(event, str(exc))
                if not self.config.get("engine", {}).get("continue_on_error", True):
                    raise
        return {"poll": poll_result, "resume": resume_result, "gate_advance": gate_advance_result, "processed": processed}
