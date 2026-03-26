from __future__ import annotations

from dataclasses import dataclass
import hashlib
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from github_pm_agent.actions import GitHubActionToolkit
from github_pm_agent.ai_adapter import AIAdapterManager
from github_pm_agent.config import gh_path, repo_names, runtime_dir
from github_pm_agent.engine import EventEngine
from github_pm_agent.github_client import GitHubClient
from github_pm_agent.poller import GitHubPoller
from github_pm_agent.prompt_library import PromptLibrary
from github_pm_agent.queue_store import QueueStore, SuspendedEventScanner
from github_pm_agent.role_registry import RoleRegistry
from github_pm_agent.session_store import SessionStore
from github_pm_agent.status_probe import StatusProbe
from github_pm_agent.models import Event
from github_pm_agent.utils import read_json, read_jsonl, utc_now_iso, write_json
from github_pm_agent.phase_gate_scanner import PhaseGateScanner
from github_pm_agent.issue_coding_sync_scanner import IssueCodingSyncScanner
from github_pm_agent.merge_conflict_scanner import MergeConflictScanner
from github_pm_agent.workflow_orchestrator import WorkflowOrchestrator


@dataclass
class RepoRuntime:
    repo: str
    client: GitHubClient
    poller: GitHubPoller
    probe: StatusProbe
    actions: GitHubActionToolkit
    engine: EventEngine


class GitHubPMAgentApp:
    def __init__(self, config: Dict[str, Any], project_root: Path) -> None:
        self.config = config
        self.project_root = project_root
        self.runtime_dir = runtime_dir(config)
        self.queue = QueueStore(self.runtime_dir)
        self.prompts = PromptLibrary(project_root)
        self.sessions = SessionStore(self.runtime_dir)
        self.ai = AIAdapterManager(project_root, config, self.prompts, self.sessions)
        self.repo_names = repo_names(config)
        self.repo_runtimes = [self._build_repo_runtime(repo) for repo in self.repo_names]
        self.client = self.repo_runtimes[0].client
        self.actions = self.repo_runtimes[0].actions
        self.engine = self.repo_runtimes[0].engine
        self.engine.role_registry = RoleRegistry(project_root)
        agent_configs = config.get("agents", [])
        dry_run = config.get("engine", {}).get("dry_run", True)
        agent_toolkits: Dict[str, Any] = {}
        for agent_cfg in agent_configs:
            token_env = agent_cfg.get("token_env")
            gh_user = agent_cfg.get("gh_user")
            agent_client = GitHubClient(gh_path(config), self.repo_names[0], token_env=token_env, gh_user=gh_user)
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
        owner_login = config.get("github", {}).get("owner", "")
        self.scanner = SuspendedEventScanner(self.queue, self.client, owner_login)
        self.gate_scanner = PhaseGateScanner(self.queue, self.client, owner_login, self.actions)
        self.issue_coding_sync_scanner = IssueCodingSyncScanner(self.queue, self.client, self.actions)
        self.merge_conflict_scanner = MergeConflictScanner(self.queue, self.client, self.actions, config)
        self.cursors_path = self.runtime_dir / "cursors.json"

    def poll(self) -> Dict[str, Any]:
        cursor = read_json(self.cursors_path, {"since": "1970-01-01T00:00:00Z"})
        since = cursor.get("since", "1970-01-01T00:00:00Z")
        followup_events = self._followup_events(now_iso=utc_now_iso())
        repo_results = [self._poll_repo(repo_runtime, since) for repo_runtime in self.repo_runtimes]
        all_events: List[Event] = []
        for result in repo_results:
            all_events.extend(result["events"])
            all_events.extend(result["synthetic_events"])
        all_events.extend(followup_events)
        enqueued = self.queue.enqueue(all_events)
        write_json(self.cursors_path, {"since": utc_now_iso()})
        if len(self.repo_runtimes) == 1:
            result = repo_results[0]
            return {
                "since": since,
                "events_found": len(result["events"]),
                "synthetic_events_found": len(result["synthetic_events"]),
                "events_enqueued": enqueued,
            }
        return {
            "since": since,
            "events_found": sum(len(result["events"]) for result in repo_results),
            "synthetic_events_found": sum(len(result["synthetic_events"]) for result in repo_results),
            "followup_events_found": len(followup_events),
            "events_enqueued": enqueued,
            "repositories": [
                {
                    "repo": result["repo"],
                    "events_found": len(result["events"]),
                    "synthetic_events_found": len(result["synthetic_events"]),
                }
                for result in repo_results
            ],
        }

    def cycle(self) -> Dict[str, Any]:
        poll_result = self.poll()
        resume_result = self.scanner.scan_and_resume()
        # Drain regular poll events first while gate/clarification state is still intact.
        # gate_scanner runs afterward and clears gate/clarification only after poll events
        # have already been blocked, preventing the race where a poll event bypasses the
        # gate/clarification check because the scanner cleared it in the same cycle.
        processed = self.drain_queue()
        workflow_sync_result = self.issue_coding_sync_scanner.scan_and_sync()
        merge_conflict_result = self.merge_conflict_scanner.scan_and_requeue()
        gate_advance_result = self.gate_scanner.scan_and_advance()
        # Drain the conflict/gate advance events produced after the main queue pass.
        processed = processed + self.drain_queue()
        return {
            "poll": poll_result,
            "resume": resume_result,
            "workflow_sync": workflow_sync_result,
            "merge_conflicts": merge_conflict_result,
            "gate_advance": gate_advance_result,
            "processed": processed,
        }

    def reconcile(self) -> Dict[str, Any]:
        followup_events = self._followup_events(now_iso=utc_now_iso())
        if followup_events:
            self.queue.enqueue(followup_events)
        return {"processed": self.drain_queue(), "followup_events_found": len(followup_events)}

    def daemon(self, interval_seconds: float = 60.0, max_cycles: Optional[int] = None, sleep_fn: Any = time.sleep) -> Dict[str, Any]:
        cycles: List[Dict[str, Any]] = []
        completed = 0
        while max_cycles is None or completed < max_cycles:
            cycle_result = self.cycle()
            cycles.append(cycle_result)
            completed += 1
            if max_cycles is not None and completed >= max_cycles:
                break
            sleep_fn(max(0.0, interval_seconds))
        return {"cycles": completed, "results": cycles}

    def ingest_webhook(self, payload: Any, event_type: str = "") -> Dict[str, Any]:
        events = self.events_from_webhook(payload, event_type=event_type)
        enqueued = self.queue.enqueue(events)
        return {"events_found": len(events), "events_enqueued": enqueued}

    def analytics(self) -> Dict[str, Any]:
        pending = self.queue.list_pending()
        done = self.queue.list_done()
        dead = self.queue.list_dead()
        action_counts: Dict[str, int] = {}
        handler_counts: Dict[str, int] = {}
        escalation_counts = {"human": 0, "automated": 0}
        for record in done:
            result = record.get("result", {}) if isinstance(record, dict) else {}
            action_type = str(result.get("action", {}).get("action_type") or result.get("action_type") or "unknown")
            action_counts[action_type] = action_counts.get(action_type, 0) + 1
            handler = str(result.get("handler", "unknown"))
            handler_counts[handler] = handler_counts.get(handler, 0) + 1
            plan = result.get("plan", {}) if isinstance(result, dict) else {}
            if isinstance(plan, dict) and plan.get("needs_human_decision"):
                escalation_counts["human"] += 1
            else:
                escalation_counts["automated"] += 1
        return {
            "repos": self.repo_names,
            "queue": {
                "pending": len(pending),
                "done": len(done),
                "dead": len(dead),
            },
            "actions": action_counts,
            "handlers": handler_counts,
            "escalations": escalation_counts,
            "memory": self._memory_analytics_snapshot(),
        }

    def drain_queue(self) -> List[Dict[str, Any]]:
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
                processed.append({"event_id": event.event_id, "repo": event.repo, "result": result})
            except Exception as exc:  # noqa: BLE001
                self.queue.mark_failed(event, str(exc))
                if not self.config.get("engine", {}).get("continue_on_error", True):
                    raise
        return processed

    def events_from_webhook(self, payload: Any, event_type: str = "") -> List[Event]:
        if isinstance(payload, list):
            events: List[Event] = []
            for item in payload:
                if isinstance(item, dict):
                    events.extend(self.events_from_webhook(item, event_type=event_type))
            return events
        if not isinstance(payload, dict):
            return []
        if {"event_id", "event_type", "source", "occurred_at", "repo"}.issubset(payload.keys()):
            return [Event.from_dict(payload)]
        event_kind = event_type or str(payload.get("action") or payload.get("event_type") or "").strip()
        if not event_kind:
            return []
        event = self._event_from_github_payload(event_kind, payload)
        return [event] if event is not None else []

    def _build_repo_runtime(self, repo: str) -> RepoRuntime:
        # Use the highest-priority agent's token for write access (gate comments, etc.)
        _primary_agent = next(
            (a for a in sorted(self.config.get("agents", []), key=lambda a: a.get("priority", 99)) if a.get("token_env") or a.get("gh_user")),
            {},
        )
        client = GitHubClient(
            gh_path(self.config),
            repo,
            token_env=_primary_agent.get("token_env"),
            gh_user=_primary_agent.get("gh_user"),
        )
        poller = GitHubPoller(
            client,
            repo,
            self.config.get("github", {}).get("default_branch", "main"),
            self.config.get("github", {}).get("mentions", []),
        )
        probe = StatusProbe(client, repo, self.config)
        actions = GitHubActionToolkit(
            client,
            self.runtime_dir,
            dry_run=self.config.get("engine", {}).get("dry_run", True),
        )
        engine = EventEngine(self.config, self.ai, actions, self.runtime_dir)
        return RepoRuntime(repo=repo, client=client, poller=poller, probe=probe, actions=actions, engine=engine)

    def _poll_repo(self, repo_runtime: RepoRuntime, since: str) -> Dict[str, Any]:
        events = repo_runtime.poller.poll(since)
        synthetic_events = repo_runtime.probe.scan()
        return {"repo": repo_runtime.repo, "events": events, "synthetic_events": synthetic_events}

    def _engine_for_repo(self, repo: str) -> EventEngine:
        for repo_runtime in self.repo_runtimes:
            if repo_runtime.repo == repo:
                return repo_runtime.engine
        return self.engine

    def _followup_events(self, now_iso: str) -> List[Event]:
        memory_loop = getattr(self.engine, "memory_loop", None)
        due_followup_events = getattr(memory_loop, "due_followup_events", None)
        if memory_loop is None or not callable(due_followup_events):
            return []
        try:
            followup_events = due_followup_events(now_iso=now_iso)
        except Exception:  # noqa: BLE001
            return []
        if isinstance(followup_events, list):
            return followup_events
        if isinstance(followup_events, tuple):
            return list(followup_events)
        return []

    def _memory_analytics_snapshot(self) -> Dict[str, Any]:
        memory_loop = getattr(self.engine, "memory_loop", None)
        if memory_loop is None or not hasattr(memory_loop, "analytics_snapshot"):
            return {}
        snapshot = memory_loop.analytics_snapshot()
        return snapshot if isinstance(snapshot, dict) else {}

    def _event_from_github_payload(self, event_kind: str, payload: Dict[str, Any]) -> Optional[Event]:
        repo = self._payload_repo(payload)
        if not repo:
            return None
        occurred_at = self._payload_timestamp(payload)
        actor = self._payload_actor(payload)
        event_id = self._payload_event_id(event_kind, payload, occurred_at)
        primary = self._payload_primary_object(event_kind, payload)
        title = str(primary.get("title") or payload.get("title") or payload.get("name") or event_kind)
        comment = payload.get("comment")
        comment_body = comment.get("body") if isinstance(comment, dict) else ""
        head_commit = payload.get("head_commit")
        commit_body = head_commit.get("message") if isinstance(head_commit, dict) else ""
        body = str(primary.get("body") or payload.get("body") or comment_body or commit_body or "")
        target_kind, target_number = self._payload_target(payload, event_kind)
        metadata = {
            "action": payload.get("action", ""),
            "repository": repo,
            "label": ((payload.get("label") or {}).get("name") if isinstance(payload.get("label"), dict) else ""),
            "labels": [
                str((label or {}).get("name", ""))
                for label in ((payload.get("issue") or payload).get("labels") or [])
                if isinstance(label, dict) and (label or {}).get("name")
            ],
        }
        return Event(
            event_id=event_id,
            event_type=self._normalize_webhook_event_type(event_kind, payload),
            source="webhook",
            occurred_at=occurred_at,
            repo=repo,
            actor=actor,
            url=str(primary.get("html_url") or primary.get("url") or payload.get("html_url") or payload.get("url") or ""),
            title=title,
            body=body,
            target_kind=target_kind,
            target_number=target_number,
            metadata=metadata,
        )

    def _payload_repo(self, payload: Dict[str, Any]) -> str:
        repository = payload.get("repository") or {}
        if isinstance(repository, dict):
            full_name = repository.get("full_name") or repository.get("name")
            if full_name:
                return str(full_name)
        repo = payload.get("repo")
        if isinstance(repo, dict):
            full_name = repo.get("name") or repo.get("full_name")
            if full_name:
                return str(full_name)
        return ""

    def _payload_actor(self, payload: Dict[str, Any]) -> str:
        for key in ("sender", "user", "author", "assignee"):
            node = payload.get(key)
            if isinstance(node, dict):
                login = node.get("login") or node.get("name")
                if login:
                    return str(login)
        return ""

    def _payload_timestamp(self, payload: Dict[str, Any]) -> str:
        for key in ("created_at", "updated_at", "timestamp"):
            value = payload.get(key)
            if isinstance(value, str) and "T" in value:
                return value
        return utc_now_iso()

    def _payload_target(self, payload: Dict[str, Any], event_kind: str) -> tuple[str, Optional[int]]:
        if event_kind.startswith("issue"):
            issue = payload.get("issue") or payload
            if isinstance(issue, dict):
                return "issue", self._payload_number(issue)
        if event_kind.startswith("pull_request"):
            pr = payload.get("pull_request") or payload
            if isinstance(pr, dict):
                return "pull_request", self._payload_number(pr)
        if event_kind.startswith("discussion"):
            discussion = payload.get("discussion") or payload
            if isinstance(discussion, dict):
                return "discussion", self._payload_number(discussion)
        if event_kind in {"push", "commit"}:
            return "commit", None
        if event_kind.startswith("workflow"):
            run = payload.get("workflow_run") or payload
            if isinstance(run, dict):
                return "workflow_run", self._payload_number(run)
        return "none", None

    def _payload_primary_object(self, event_kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if event_kind == "issues" and isinstance(payload.get("issue"), dict):
            return payload["issue"]
        if event_kind == "pull_request" and isinstance(payload.get("pull_request"), dict):
            return payload["pull_request"]
        if event_kind == "discussion" and isinstance(payload.get("discussion"), dict):
            return payload["discussion"]
        if event_kind == "workflow_run" and isinstance(payload.get("workflow_run"), dict):
            return payload["workflow_run"]
        return payload

    def _payload_number(self, payload: Dict[str, Any]) -> Optional[int]:
        for key in ("number", "id", "run_number"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    def _payload_event_id(self, event_kind: str, payload: Dict[str, Any], occurred_at: str) -> str:
        raw_id = payload.get("id") or payload.get("node_id") or payload.get("sha") or payload.get("number") or event_kind
        digest = hashlib.sha1(f"{event_kind}:{raw_id}:{occurred_at}".encode("utf-8")).hexdigest()
        return f"webhook:{digest}"

    def _normalize_webhook_event_type(self, event_kind: str, payload: Dict[str, Any]) -> str:
        action = str(payload.get("action") or "").lower()
        if event_kind == "issues":
            issue_payload = payload.get("issue") or payload
            issue_state = str(issue_payload.get("state") or payload.get("state") or "").lower()
            labels = [str((label or {}).get("name", "")) for label in issue_payload.get("labels", []) if isinstance(label, dict)]
            label_name = str((payload.get("label") or {}).get("name") or "")
            if issue_state != "closed" and action == "labeled" and label_name == "ready-to-code":
                return "issue_coding"
            if issue_state != "closed" and action == "reopened" and "ready-to-code" in labels:
                return "issue_coding"
            if action in {"closed", "reopened", "assigned", "unassigned", "labeled", "unlabeled", "milestoned", "demilestoned"}:
                return f"issue_event_{action}"
            return "issue_changed"
        if event_kind == "issue_comment":
            return "issue_comment"
        if event_kind == "pull_request":
            if action == "review_requested":
                return "issue_event_review_requested"
            return "pull_request_changed"
        if event_kind == "pull_request_review":
            return "pull_request_review"
        if event_kind == "discussion":
            return "discussion"
        if event_kind == "discussion_comment":
            return "discussion_comment"
        if event_kind == "workflow_run":
            conclusion = str((payload.get("workflow_run") or {}).get("conclusion") or payload.get("conclusion") or "").lower()
            return "workflow_failed" if conclusion == "failure" else "workflow_run"
        if event_kind == "push":
            return "commit"
        return event_kind
