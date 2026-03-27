from __future__ import annotations

import base64
import hashlib
import re
from typing import Any, Dict, List, Optional

from github_pm_agent.models import Event
from github_pm_agent.utils import utc_now_iso
from github_pm_agent.workflow_instance import WorkflowInstance


class ProjectReleaseScanner:
    """Enqueue a deterministic release event once a managed project run is fully complete."""

    REQUIRED_README_SECTIONS = {
        "overview": (
            r"^##+\s+(overview|what it does|功能介绍|项目介绍)\b",
            r"^##+\s+(features|capabilities|核心功能)\b",
        ),
        "install": (
            r"^##+\s+(install|installation|setup|安装)\b",
            r"^##+\s+(quick start|快速开始)\b",
        ),
        "run": (
            r"^##+\s+(run|usage|local run|运行|用法)\b",
            r"^##+\s+(quick start|快速开始)\b",
        ),
        "deployment": (
            r"^##+\s+(deployment|deploy|部署)\b",
        ),
    }

    def __init__(
        self,
        queue: Any,
        clients_by_repo: Dict[str, Any],
        config: Dict[str, Any],
    ) -> None:
        self.queue = queue
        self.clients_by_repo = dict(clients_by_repo)
        self.config = config

    def scan_and_enqueue(self) -> List[Dict[str, Any]]:
        workflows_dir = self.queue.runtime_dir / "workflows"
        if not workflows_dir.exists():
            return []

        results: List[Dict[str, Any]] = []
        for repo_dir in workflows_dir.iterdir():
            if not repo_dir.is_dir():
                continue
            repo = repo_dir.name.replace("__", "/", 1)
            client = self.clients_by_repo.get(repo)
            if client is None:
                continue

            release_event = self._build_release_event(repo, repo_dir, client)
            if release_event is None:
                continue
            if self.queue.enqueue([release_event]) != 1:
                continue
            results.append(
                {
                    "repo": repo,
                    "event_id": release_event.event_id,
                    "tag_name": release_event.metadata.get("tag_name", ""),
                    "merged_pr_count": release_event.metadata.get("merged_pr_count", 0),
                }
            )
        return results

    def _build_release_event(self, repo: str, repo_dir: Any, client: Any) -> Optional[Event]:
        discussion_complete = False
        issue_coding_instances: List[WorkflowInstance] = []
        for state_path in repo_dir.glob("*/state.json"):
            instance = WorkflowInstance(state_path)
            workflow_type = instance.get_workflow_type()
            if workflow_type == "discussion" and instance.is_completed():
                discussion_complete = True
            elif workflow_type == "issue_coding":
                issue_coding_instances.append(instance)

        if not discussion_complete or not issue_coding_instances:
            return None
        if not all(instance.is_completed() for instance in issue_coding_instances):
            return None

        open_issues = self._open_business_issues(client, repo)
        if open_issues:
            return None
        open_prs = self._open_pull_requests(client, repo)
        if open_prs:
            return None

        releases = self._releases(client, repo)
        latest_release = releases[0] if releases else None
        merged_prs = self._merged_pull_requests(client, repo)
        if not merged_prs:
            return None

        unreleased_prs = self._unreleased_pull_requests(merged_prs, latest_release)
        if not unreleased_prs:
            return None

        docs_status = self._readme_release_status(client, repo)
        if docs_status is not None:
            return None

        tag_name = self._next_release_tag(latest_release)
        release_name = f"Release {tag_name}"
        release_body = self._build_release_body(unreleased_prs)
        default_branch = self.config.get("github", {}).get("default_branch", "main")
        merge_marker = unreleased_prs[-1].get("merged_at") or unreleased_prs[-1].get("updated_at") or utc_now_iso()
        seed = f"{repo}:{tag_name}:{merge_marker}:{len(unreleased_prs)}"
        event_id = f"project_release_ready:{hashlib.sha1(seed.encode('utf-8')).hexdigest()}"

        return Event(
            event_id=event_id,
            event_type="project_release_ready",
            source="project_release_scanner",
            occurred_at=utc_now_iso(),
            repo=repo,
            actor="github-pm-agent",
            url=f"https://github.com/{repo}",
            title=f"{repo} ready for release {tag_name}",
            body=release_body,
            target_kind="repo",
            target_number=None,
            metadata={
                "tag_name": tag_name,
                "release_name": release_name,
                "release_body": release_body,
                "target_commitish": default_branch,
                "merged_pr_count": len(unreleased_prs),
                "latest_release_tag": (latest_release or {}).get("tag_name", ""),
                "default_branch": default_branch,
            },
        )

    def _readme_release_status(self, client: Any, repo: str) -> Optional[Dict[str, Any]]:
        readme_text = self._readme_text(client, repo)
        if not readme_text.strip():
            return {"reason": "missing_readme"}
        missing = [
            section
            for section in self.REQUIRED_README_SECTIONS
            if not self._has_required_section(readme_text, self.REQUIRED_README_SECTIONS[section])
        ]
        if missing:
            return {"reason": "missing_readme_sections", "missing_sections": missing}
        return None

    def _readme_text(self, client: Any, repo: str) -> str:
        try:
            payload = client.api(f"repos/{repo}/readme", method="GET")
        except Exception:
            return ""
        if not isinstance(payload, dict):
            return ""
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            try:
                normalized = content.replace("\n", "")
                return base64.b64decode(normalized).decode("utf-8", errors="replace")
            except Exception:
                return ""
        return ""

    def _has_required_section(self, readme_text: str, patterns: tuple[str, ...]) -> bool:
        for pattern in patterns:
            if re.search(pattern, readme_text, flags=re.IGNORECASE | re.MULTILINE):
                return True
        return False

    def _open_business_issues(self, client: Any, repo: str) -> List[Dict[str, Any]]:
        issues = client.api(f"repos/{repo}/issues", {"state": "open", "per_page": 100}, method="GET")
        result: List[Dict[str, Any]] = []
        for issue in issues if isinstance(issues, list) else []:
            if issue.get("pull_request"):
                continue
            labels = {
                str((label or {}).get("name", "")).strip()
                for label in issue.get("labels", [])
                if isinstance(label, dict)
            }
            if labels & {"workflow-gate", "agent-escalate"}:
                continue
            result.append(issue)
        return result

    def _open_pull_requests(self, client: Any, repo: str) -> List[Dict[str, Any]]:
        pulls = client.api(f"repos/{repo}/pulls", {"state": "open", "per_page": 100}, method="GET")
        return list(pulls) if isinstance(pulls, list) else []

    def _releases(self, client: Any, repo: str) -> List[Dict[str, Any]]:
        releases = client.api(f"repos/{repo}/releases", {"per_page": 100}, method="GET")
        return list(releases) if isinstance(releases, list) else []

    def _merged_pull_requests(self, client: Any, repo: str) -> List[Dict[str, Any]]:
        pulls = client.api(f"repos/{repo}/pulls", {"state": "closed", "per_page": 100}, method="GET")
        merged = [pr for pr in pulls if isinstance(pr, dict) and pr.get("merged_at")] if isinstance(pulls, list) else []
        merged.sort(key=lambda pr: str(pr.get("merged_at") or pr.get("updated_at") or ""))
        return merged

    def _unreleased_pull_requests(
        self,
        merged_prs: List[Dict[str, Any]],
        latest_release: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if latest_release is None:
            return merged_prs
        cutoff = str(
            latest_release.get("published_at")
            or latest_release.get("created_at")
            or latest_release.get("updated_at")
            or ""
        )
        return [
            pr
            for pr in merged_prs
            if str(pr.get("merged_at") or pr.get("updated_at") or "") > cutoff
        ]

    def _next_release_tag(self, latest_release: Optional[Dict[str, Any]]) -> str:
        if latest_release is None:
            return "v0.1.0"
        current = str(latest_release.get("tag_name") or "").strip()
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", current)
        if not match:
            return "v0.1.0"
        major, minor, patch = (int(part) for part in match.groups())
        return f"v{major}.{minor}.{patch + 1}"

    def _build_release_body(self, merged_prs: List[Dict[str, Any]]) -> str:
        lines = [
            "Automated release after all managed implementation workflows completed.",
            "",
            "## Included pull requests",
        ]
        for pr in merged_prs:
            number = pr.get("number")
            title = str(pr.get("title") or "").strip() or "Untitled PR"
            lines.append(f"- #{number} {title}")
        return "\n".join(lines).strip()
