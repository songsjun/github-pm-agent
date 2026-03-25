"""project_initializer.py — bootstraps a brand-new GitHub project from raw requirements.

Account model
-------------
- PM agent   (agents[role=pm])  — single bot; creates the repo under its own GitHub account.
- Worker agents                  — one or more bots; added as collaborators.
- Customer   (github.customer)   — the human user who provided requirements;
                                   added as collaborator so they can follow and confirm gates.

Entry point: ProjectInitializer(config, project_root).initialize(requirements_text)

Steps
-----
1. AI generates project name / slug / description from requirements.
2. PM agent creates the GitHub repo under its own account ({pm_gh_user}/{slug}).
3. Configures branch protection: PRs required, 1 approval, no direct push to main.
4. Enables Discussions on the repo.
5. Adds customer account (github.customer) as collaborator.
6. Adds every worker agent as collaborator; each auto-accepts their invitation.
7. Posts the initial kickoff Discussion to trigger the event-driven workflow.
8. Writes a project-specific config file at config/{slug}.json based on the template.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ProjectInitializer:
    def __init__(self, config: Dict[str, Any], project_root: Path) -> None:
        self.config = config
        self.project_root = Path(project_root)
        self.gh_path = config.get("github", {}).get("gh_path", "gh")
        self.github_cfg = config.get("github", {})
        self.visibility = self.github_cfg.get("repo_visibility", "private")

        # PM agent is the GitHub account that physically owns the repo
        self._pm_agent = self._find_pm_agent()
        if not self._pm_agent:
            raise ValueError("config must include an agent with role=pm")
        self.pm_gh_user = (self._pm_agent.get("gh_user") or "").strip()
        if not self.pm_gh_user:
            raise ValueError("PM agent must have gh_user set (it becomes the repo owner)")

        # Customer = human user, added as collaborator
        self.customer = (self.github_cfg.get("customer") or self.github_cfg.get("owner", "")).strip()

        self._pm_token: Optional[str] = self._resolve_agent_token(self._pm_agent)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def initialize(self, requirements: str) -> Dict[str, Any]:
        """Run the full bootstrap sequence.  Returns a summary dict."""
        # Step 1 — generate project info via AI
        logger.info("Generating project name from requirements...")
        project_info = self._ai_generate_project_info(requirements)
        slug = project_info["slug"]
        full_repo = f"{self.pm_gh_user}/{slug}"
        logger.info("Target repo: %s (owned by PM agent)", full_repo)

        # Step 2 — PM agent creates the repo under its own account
        logger.info("Creating repository as PM agent %s...", self.pm_gh_user)
        self._create_repo(slug, project_info.get("description", ""))

        # Short pause so GitHub propagates the default branch before we set protection
        time.sleep(2)

        # Step 3 — branch protection
        logger.info("Setting up branch protection...")
        try:
            self._setup_branch_protection(full_repo)
        except Exception as exc:
            logger.warning("Branch protection setup failed (non-fatal): %s", exc)

        # Step 4 — enable discussions
        logger.info("Enabling Discussions...")
        try:
            self._enable_discussions(full_repo)
        except Exception as exc:
            logger.warning("Enable discussions failed (non-fatal): %s", exc)

        added_collaborators: List[str] = []

        # Step 5 — add customer as collaborator (maintain permission so they can confirm gates)
        if self.customer:
            logger.info("Adding customer %s as collaborator...", self.customer)
            try:
                self._add_collaborator(full_repo, self.customer, permission="maintain")
                added_collaborators.append(self.customer)
            except Exception as exc:
                logger.warning("Failed to add customer %s: %s", self.customer, exc)

        # Step 6 — add worker agents as collaborators + auto-accept invitations
        for agent in self.config.get("agents", []):
            if agent.get("role") == "pm":
                continue  # PM created the repo; no invitation needed
            username = (agent.get("username") or agent.get("gh_user", "")).strip()
            if not username:
                continue
            logger.info("Adding worker agent %s as collaborator...", username)
            try:
                self._add_collaborator(full_repo, username)
                added_collaborators.append(username)
            except Exception as exc:
                logger.warning("Failed to add worker %s: %s", username, exc)
                continue
            try:
                self._accept_invitation(full_repo, agent)
            except Exception as exc:
                logger.warning("Failed to accept invitation for %s: %s", username, exc)

        # Step 7 — create initial discussion to kick off the workflow
        logger.info("Creating kickoff discussion...")
        discussion: Dict[str, Any] = {}
        try:
            discussion = self._create_initial_discussion(full_repo, requirements, project_info)
        except Exception as exc:
            logger.warning("Discussion creation failed (non-fatal): %s", exc)

        # Step 8 — save project config
        config_path = self._save_project_config(full_repo, slug)
        logger.info("Project config saved to %s", config_path)

        return {
            "repo": full_repo,
            "slug": slug,
            "discussion_url": discussion.get("url", ""),
            "collaborators_added": added_collaborators,
            "config_path": str(config_path),
            "next_step": f"github-pm-agent daemon --config {config_path}",
        }

    def _find_pm_agent(self) -> Optional[Dict[str, Any]]:
        for agent in self.config.get("agents", []):
            if agent.get("role") == "pm":
                return agent
        return None

    # ------------------------------------------------------------------
    # AI helpers
    # ------------------------------------------------------------------

    def _ai_generate_project_info(self, requirements: str) -> Dict[str, Any]:
        """Call the configured AI provider to derive project name / slug / description."""
        try:
            from github_pm_agent.ai_adapter import AIAdapterManager, AiRequest
            from github_pm_agent.prompt_library import PromptLibrary
            from github_pm_agent.session_store import SessionStore
            from github_pm_agent.utils import ensure_dir

            tmp_runtime = self.project_root / "runtime" / "_start_tmp"
            ensure_dir(tmp_runtime)

            prompts = PromptLibrary(self.project_root)
            sessions = SessionStore(tmp_runtime)
            ai = AIAdapterManager(self.project_root, self.config, prompts, sessions)

            user_prompt = (
                "Given these software requirements, generate a GitHub-friendly project name.\n\n"
                f"Requirements:\n{requirements[:3000]}\n\n"
                "Output ONLY a JSON object with exactly these fields:\n"
                '{"name": "Human Readable Project Name", '
                '"slug": "github-repo-slug-lowercase-hyphens", '
                '"description": "One sentence description under 100 chars"}'
            )
            request = AiRequest(
                system_prompt="You are a project naming assistant. Output ONLY valid JSON.",
                user_prompt=user_prompt,
                provider=self.config.get("ai", {}).get("default_provider", ""),
                model=self.config.get("ai", {}).get("default_model", ""),
                session_id="start_project_naming",
                event_id="start",
            )
            response = ai.generate(request)
            text = response.content or ""
            match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
            if match:
                data = json.loads(match.group())
                if data.get("slug"):
                    return data
        except Exception as exc:
            logger.warning("AI project naming failed (%s); falling back to heuristic.", exc)

        # Heuristic fallback
        first_line = requirements.strip().split('\n')[0].lstrip('#').strip()[:60]
        slug = re.sub(r'[^a-z0-9]+', '-', first_line.lower()).strip('-') or "new-project"
        return {"name": first_line or "New Project", "slug": slug, "description": first_line[:100]}

    # ------------------------------------------------------------------
    # GitHub operations
    # ------------------------------------------------------------------

    def _resolve_agent_token(self, agent: Dict[str, Any]) -> Optional[str]:
        token_env = agent.get("token_env")
        if token_env:
            token = os.environ.get(token_env)
            if token:
                return token
        gh_user = agent.get("gh_user", "").strip()
        if gh_user:
            try:
                result = subprocess.run(
                    [self.gh_path, "auth", "token", "--user", gh_user],
                    check=True, capture_output=True, text=True,
                )
                token = result.stdout.strip()
                if token:
                    return token
            except subprocess.CalledProcessError:
                pass
        return None

    def _gh_run(self, args: List[str], token: Optional[str] = None, input_data: Optional[str] = None) -> Any:
        command = [self.gh_path] + args
        env = {**os.environ}
        if token:
            env["GITHUB_TOKEN"] = token
        result = subprocess.run(
            command,
            input=input_data,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        output = result.stdout.strip()
        if not output:
            return {}
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {"raw": output}

    def _gh_api(
        self,
        path: str,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        json_body: Optional[Any] = None,
    ) -> Any:
        if json_body is not None:
            return self._gh_run(
                ["api", path, "--method", method, "--input", "-"],
                token=token,
                input_data=json.dumps(json_body),
            )
        args = ["api", path, "--method", method]
        for key, value in (params or {}).items():
            if isinstance(value, bool):
                args.extend(["-F", f"{key}={'true' if value else 'false'}"])
            elif isinstance(value, (int, float)):
                args.extend(["-F", f"{key}={value}"])
            else:
                args.extend(["-f", f"{key}={value}"])
        return self._gh_run(args, token=token)

    def _gh_graphql(self, query: str, variables: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
        args = ["api", "graphql", "-f", f"query={query}"]
        for key, value in (variables or {}).items():
            if isinstance(value, (int, bool)):
                args.extend(["-F", f"{key}={value}"])
            else:
                args.extend(["-f", f"{key}={value}"])
        return self._gh_run(args, token=token)

    def _create_repo(self, slug: str, description: str) -> None:
        full_name = f"{self.owner}/{slug}"
        visibility_flag = f"--{self.visibility}"
        args = [
            "repo", "create", full_name,
            visibility_flag,
            "--description", description or slug,
            "--add-readme",
        ]
        self._gh_run(args, token=self._pm_token)

    def _setup_branch_protection(self, full_repo: str) -> None:
        repo_data = self._gh_api(f"repos/{full_repo}", token=self._pm_token)
        default_branch = repo_data.get("default_branch", "main")
        protection_payload = {
            "required_status_checks": None,
            "enforce_admins": False,
            "required_pull_request_reviews": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews": False,
                "require_code_owner_reviews": False,
            },
            "restrictions": None,
            "allow_force_pushes": False,
            "allow_deletions": False,
        }
        self._gh_api(
            f"repos/{full_repo}/branches/{default_branch}/protection",
            method="PUT",
            token=self._pm_token,
            json_body=protection_payload,
        )

    def _enable_discussions(self, full_repo: str) -> None:
        self._gh_api(
            f"repos/{full_repo}",
            method="PATCH",
            params={"has_discussions": True},
            token=self._pm_token,
        )

    def _add_collaborator(self, full_repo: str, username: str, permission: str = "write") -> None:
        self._gh_api(
            f"repos/{full_repo}/collaborators/{username}",
            method="PUT",
            params={"permission": permission},
            token=self._pm_token,
        )

    def _accept_invitation(self, full_repo: str, agent: Dict[str, Any]) -> None:
        worker_token = self._resolve_agent_token(agent)
        if not worker_token:
            return
        invitations = self._gh_api("user/repository_invitations", token=worker_token)
        if not isinstance(invitations, list):
            return
        target = full_repo.lower()
        for inv in invitations:
            inv_repo = (inv.get("repository") or {}).get("full_name", "").lower()
            if inv_repo == target:
                inv_id = inv.get("id")
                if inv_id:
                    self._gh_api(
                        f"user/repository_invitations/{inv_id}",
                        method="PATCH",
                        token=worker_token,
                    )
                break

    def _get_repo_node_id(self, full_repo: str) -> str:
        data = self._gh_api(f"repos/{full_repo}", token=self._pm_token)
        return data.get("node_id", "")

    def _get_discussion_category_id(self, full_repo: str) -> str:
        owner, name = full_repo.split("/", 1)
        query = """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            discussionCategories(first: 10) {
              nodes { id name }
            }
          }
        }
        """
        result = self._gh_graphql(query, {"owner": owner, "name": name}, token=self._pm_token)
        nodes = (
            result.get("data", {})
            .get("repository", {})
            .get("discussionCategories", {})
            .get("nodes", [])
        )
        preferred = {"general", "announcements", "ideas", "show and tell"}
        for node in nodes:
            if node.get("name", "").lower() in preferred:
                return node["id"]
        return nodes[0]["id"] if nodes else ""

    def _create_initial_discussion(
        self, full_repo: str, requirements: str, project_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        # Discussions become available after a short delay — retry a few times
        for attempt in range(4):
            try:
                repo_id = self._get_repo_node_id(full_repo)
                category_id = self._get_discussion_category_id(full_repo)
                if repo_id and category_id:
                    break
            except Exception:
                pass
            time.sleep(3)
        else:
            logger.warning("Could not obtain repo node_id or category_id for discussion creation")
            return {}

        project_name = project_info.get("name", full_repo.split("/")[-1])
        title = f"Kickoff: {project_name}"
        body = self._build_kickoff_body(requirements, project_info)

        mutation = """
        mutation($repositoryId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
          createDiscussion(input: {
            repositoryId: $repositoryId,
            categoryId: $categoryId,
            title: $title,
            body: $body
          }) {
            discussion { id url title number }
          }
        }
        """
        result = self._gh_graphql(
            mutation,
            {"repositoryId": repo_id, "categoryId": category_id, "title": title, "body": body},
            token=self._pm_token,
        )
        return result.get("data", {}).get("createDiscussion", {}).get("discussion", {})

    @staticmethod
    def _build_kickoff_body(requirements: str, project_info: Dict[str, Any]) -> str:
        description = project_info.get("description", "")
        header = f"> {description}\n\n" if description else ""
        return (
            f"{header}"
            "## Requirements\n\n"
            f"{requirements.strip()}\n\n"
            "---\n\n"
            "_This discussion was automatically created by the PM agent to kick off the "
            "product development workflow. Analysis and structured discovery will begin shortly._"
        )

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _save_project_config(self, full_repo: str, slug: str) -> Path:
        new_config = {k: v for k, v in self.config.items() if not k.startswith("_")}
        new_config.setdefault("github", {})
        new_config["github"]["repo"] = full_repo
        new_config["github"]["repos"] = [full_repo]
        config_dir = self.project_root / "config"
        config_dir.mkdir(exist_ok=True)
        config_path = config_dir / f"{slug}.json"
        config_path.write_text(
            json.dumps(new_config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return config_path
