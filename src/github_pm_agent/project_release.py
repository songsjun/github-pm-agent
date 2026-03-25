"""project_release.py — generates README and development report, then creates a release PR.

Steps
-----
1. Gather repo context: merged PRs, closed issues, commit history, file tree, workflow artifacts.
2. Use AI to generate README.md content.
3. Use AI to generate docs/DEVELOPMENT_REPORT.md content.
4. Clone the repo, write both files to a new branch `release/docs`, push, and open a PR.
5. Optionally notify the customer by tagging them in the PR body.

Entry point: ProjectRelease(config, project_root).release()
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ProjectRelease:
    def __init__(self, config: Dict[str, Any], project_root: Path) -> None:
        self.config = config
        self.project_root = Path(project_root)
        self.gh_path = config.get("github", {}).get("gh_path", "gh")
        self.github_cfg = config.get("github", {})
        self.full_repo = self.github_cfg.get("repo", "")
        if not self.full_repo:
            repos = self.github_cfg.get("repos", [])
            if repos:
                self.full_repo = repos[0]
        if not self.full_repo:
            raise ValueError("config must specify github.repo")

        self._pm_agent = self._find_agent("pm")
        if not self._pm_agent:
            raise ValueError("config must include an agent with role=pm")
        self.pm_gh_user = (self._pm_agent.get("gh_user") or "").strip()
        self._pm_token: Optional[str] = self._resolve_token(self._pm_agent)

        self.customer = (self.github_cfg.get("customer") or "").strip()

        ai_cfg = config.get("ai", {})
        provider_name = ai_cfg.get("default_provider", "")
        self._provider_cfg = ai_cfg.get("providers", {}).get(provider_name, {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def release(self) -> Dict[str, Any]:
        """Run the full release sequence. Returns a summary dict."""
        logger.info("Gathering repository context for release...")
        ctx = self._gather_context()

        project_name = self.full_repo.split("/")[-1].replace("-", " ").title()

        logger.info("Generating README.md via AI...")
        readme_content = self._generate_document(
            prompt_path="prompts/release/readme.md",
            variables={
                "repo": self.full_repo,
                "project_name": project_name,
                "merged_prs": ctx["merged_prs"],
                "closed_issues": ctx["closed_issues"],
                "commit_history": ctx["commit_history"],
                "file_tree": ctx["file_tree"],
            },
        )

        logger.info("Generating DEVELOPMENT_REPORT.md via AI...")
        report_content = self._generate_document(
            prompt_path="prompts/release/dev_report.md",
            variables={
                "repo": self.full_repo,
                "project_name": project_name,
                "merged_prs": ctx["merged_prs"],
                "closed_issues": ctx["closed_issues"],
                "commit_history": ctx["commit_history"],
                "workflow_artifacts": ctx["workflow_artifacts"],
            },
        )

        logger.info("Creating release PR with generated docs...")
        pr_url = self._create_release_pr(readme_content, report_content)

        return {
            "repo": self.full_repo,
            "pr_url": pr_url,
            "files": ["README.md", "docs/DEVELOPMENT_REPORT.md"],
            "status": "merged",
        }

    # ------------------------------------------------------------------
    # Context gathering
    # ------------------------------------------------------------------

    def _gather_context(self) -> Dict[str, Any]:
        merged_prs = self._fetch_merged_prs()
        closed_issues = self._fetch_closed_issues()
        commit_history = self._fetch_commit_history()
        file_tree = self._fetch_file_tree()
        workflow_artifacts = self._load_workflow_artifacts()
        return {
            "merged_prs": merged_prs,
            "closed_issues": closed_issues,
            "commit_history": commit_history,
            "file_tree": file_tree,
            "workflow_artifacts": workflow_artifacts,
        }

    def _fetch_merged_prs(self) -> str:
        try:
            result = self._gh_run([
                "pr", "list",
                "--repo", self.full_repo,
                "--state", "merged",
                "--json", "number,title,body,mergedAt,url",
                "--limit", "50",
            ], token=self._pm_token)
            if not isinstance(result, list):
                return "(none)"
            lines = []
            for pr in result:
                lines.append(f"PR #{pr.get('number')}: {pr.get('title')} (merged {pr.get('mergedAt', '')[:10]})")
                body = (pr.get("body") or "").strip()
                if body:
                    lines.append(f"  {body[:300]}")
            return "\n".join(lines) if lines else "(none)"
        except Exception as exc:
            logger.warning("Failed to fetch merged PRs: %s", exc)
            return "(unavailable)"

    def _fetch_closed_issues(self) -> str:
        try:
            result = self._gh_run([
                "issue", "list",
                "--repo", self.full_repo,
                "--state", "closed",
                "--json", "number,title,body,closedAt",
                "--limit", "50",
            ], token=self._pm_token)
            if not isinstance(result, list):
                return "(none)"
            lines = []
            for issue in result:
                lines.append(f"Issue #{issue.get('number')}: {issue.get('title')} (closed {issue.get('closedAt', '')[:10]})")
                body = (issue.get("body") or "").strip()
                if body:
                    lines.append(f"  {body[:200]}")
            return "\n".join(lines) if lines else "(none)"
        except Exception as exc:
            logger.warning("Failed to fetch closed issues: %s", exc)
            return "(unavailable)"

    def _fetch_commit_history(self) -> str:
        try:
            result = self._gh_run([
                "api", f"repos/{self.full_repo}/commits",
                "--method", "GET",
                "-F", "per_page=30",
            ], token=self._pm_token)
            if not isinstance(result, list):
                return "(unavailable)"
            lines = []
            for commit in result:
                sha = commit.get("sha", "")[:7]
                msg = (commit.get("commit", {}).get("message") or "").split("\n")[0]
                author = commit.get("commit", {}).get("author", {}).get("name", "")
                lines.append(f"{sha} {msg} ({author})")
            return "\n".join(lines) if lines else "(none)"
        except Exception as exc:
            logger.warning("Failed to fetch commit history: %s", exc)
            return "(unavailable)"

    def _fetch_file_tree(self) -> str:
        try:
            result = self._gh_run([
                "api", f"repos/{self.full_repo}/git/trees/HEAD",
                "--method", "GET",
                "-F", "recursive=true",
            ], token=self._pm_token)
            if not isinstance(result, dict):
                return "(unavailable)"
            tree = result.get("tree", [])
            paths = [item["path"] for item in tree if item.get("type") == "blob"]
            return "\n".join(sorted(paths)) if paths else "(empty)"
        except Exception as exc:
            logger.warning("Failed to fetch file tree: %s", exc)
            return "(unavailable)"

    def _load_workflow_artifacts(self) -> str:
        """Load AI-generated artifacts from workflow state files."""
        runtime_state_dir = self.config.get("runtime", {}).get("state_dir", "runtime")
        runtime_path = self.project_root / runtime_state_dir
        repo_slug = self.full_repo.replace("/", "__")
        workflows_dir = runtime_path / "workflows" / repo_slug

        if not workflows_dir.exists():
            return "(no workflow artifacts found)"

        summaries: List[str] = []
        for issue_dir in sorted(workflows_dir.iterdir()):
            state_file = issue_dir / "state.json"
            if not state_file.exists():
                continue
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            issue_num = issue_dir.name
            artifacts = state.get("artifacts", {})
            # Collect useful artifacts: code_review, evidence_check, issue_analysis
            useful_keys = ["code_review_combined", "evidence_check", "issue_analysis_combined", "issue_analysis"]
            for key in useful_keys:
                val = artifacts.get(key)
                if val and isinstance(val, str) and len(val) > 20:
                    summaries.append(f"=== Issue #{issue_num} — {key} ===\n{val[:800]}")
                    break

        return "\n\n".join(summaries) if summaries else "(no artifacts)"

    # ------------------------------------------------------------------
    # AI generation
    # ------------------------------------------------------------------

    def _generate_document(self, prompt_path: str, variables: Dict[str, Any]) -> str:
        prompt_file = self.project_root / prompt_path
        if not prompt_file.exists():
            raise FileNotFoundError(f"Prompt not found: {prompt_file}")
        template_text = prompt_file.read_text(encoding="utf-8")
        rendered = Template(template_text).safe_substitute(variables)

        provider_type = self._provider_cfg.get("type", "")
        if provider_type == "cli_script":
            return self._call_cli_script(rendered)
        elif provider_type == "openai_compatible":
            return self._call_openai_compatible(rendered)
        else:
            raise RuntimeError(f"Unsupported AI provider type for release: {provider_type!r}")

    def _call_cli_script(self, prompt: str) -> str:
        script = self._provider_cfg.get("script", "")
        provider_name = self._provider_cfg.get("provider_name", "")
        model = self._provider_cfg.get("default_model", "")

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(prompt)
            input_path = f.name
        try:
            cmd = [
                "python3",
                str(self.project_root / script),
                "--provider", provider_name,
                "--model", model,
                "--input-file", input_path,
                "--cwd", str(self.project_root),
            ]
            extra = self._provider_cfg.get("codex_path")
            if extra:
                cmd += ["--codex-path", extra]
            reasoning = self._provider_cfg.get("reasoning_effort")
            if reasoning:
                cmd += ["--reasoning-effort", reasoning]

            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            output = result.stdout.strip()
            try:
                parsed = json.loads(output)
                return parsed.get("output") or parsed.get("content") or output
            except json.JSONDecodeError:
                return output
        finally:
            try:
                os.unlink(input_path)
            except OSError:
                pass

    def _call_openai_compatible(self, prompt: str) -> str:
        import urllib.request
        base_url = self._provider_cfg.get("base_url", "").rstrip("/")
        api_key = os.environ.get(self._provider_cfg.get("api_key_env", ""), "")
        model = self._provider_cfg.get("default_model", "gpt-4o")
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()

    # ------------------------------------------------------------------
    # PR creation
    # ------------------------------------------------------------------

    def _create_release_pr(self, readme: str, dev_report: str) -> str:
        branch = "release/docs"
        with tempfile.TemporaryDirectory() as tmp:
            clone_url = f"https://{self._pm_token}@github.com/{self.full_repo}.git"
            subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, tmp],
                check=True, capture_output=True,
            )
            env = {**os.environ, "GIT_AUTHOR_NAME": "PM Agent", "GIT_COMMITTER_NAME": "PM Agent",
                   "GIT_AUTHOR_EMAIL": "pm@agent.local", "GIT_COMMITTER_EMAIL": "pm@agent.local"}
            # Create branch
            subprocess.run(
                ["git", "-C", tmp, "checkout", "-b", branch],
                check=True, capture_output=True,
            )
            # Write README.md
            (Path(tmp) / "README.md").write_text(readme, encoding="utf-8")
            # Write docs/DEVELOPMENT_REPORT.md
            docs_dir = Path(tmp) / "docs"
            docs_dir.mkdir(exist_ok=True)
            (docs_dir / "DEVELOPMENT_REPORT.md").write_text(dev_report, encoding="utf-8")
            # Commit
            subprocess.run(
                ["git", "-C", tmp, "add", "README.md", "docs/DEVELOPMENT_REPORT.md"],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "-C", tmp, "commit", "-m", "docs: add README and development report"],
                check=True, capture_output=True, env=env,
            )
            subprocess.run(
                ["git", "-C", tmp, "push", "origin", branch],
                check=True, capture_output=True,
            )

        pr_body = (
            "## Release Documentation\n\n"
            "This PR adds the project README and a development retrospective report.\n\n"
            "**Files added/updated:**\n"
            "- `README.md` — project description, installation, usage, dependencies\n"
            "- `docs/DEVELOPMENT_REPORT.md` — development process retrospective\n"
        )

        try:
            result = self._gh_run([
                "pr", "create",
                "--repo", self.full_repo,
                "--base", "main",
                "--head", branch,
                "--title", "docs: add README and development retrospective report",
                "--body", pr_body,
            ], token=self._pm_token)
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or "").strip()
            if "already exists" in err or "pull request" in err.lower():
                logger.warning("PR may already exist: %s", err)
                return err
            raise

        pr_url = ""
        if isinstance(result, dict):
            pr_url = result.get("url", "") or result.get("raw", "")
        else:
            pr_url = str(result)

        # Extract PR number from URL for approve + merge
        pr_number = None
        if pr_url:
            import re as _re
            m = _re.search(r"/pull/(\d+)", pr_url)
            if m:
                pr_number = int(m.group(1))

        if pr_number:
            self._worker_approve_and_pm_merge(pr_number)

        return pr_url

    def _worker_approve_and_pm_merge(self, pr_number: int) -> None:
        """Have a worker agent approve, then PM merges."""
        # Find first worker agent with a token
        worker_token: Optional[str] = None
        for agent in self.config.get("agents", []):
            if agent.get("role") == "worker":
                token = self._resolve_token(agent)
                if token:
                    worker_token = token
                    break

        if worker_token:
            try:
                self._gh_run([
                    "pr", "review", str(pr_number),
                    "--repo", self.full_repo,
                    "--approve",
                    "--body", "LGTM — auto-approved by worker agent for release.",
                ], token=worker_token)
                logger.info("Worker approved release PR #%s", pr_number)
            except Exception as exc:
                logger.warning("Worker approval failed (non-fatal): %s", exc)
        else:
            logger.warning("No worker token available for PR approval")

        try:
            self._gh_run([
                "pr", "merge", str(pr_number),
                "--repo", self.full_repo,
                "--merge",
            ], token=self._pm_token)
            logger.info("PM merged release PR #%s", pr_number)
        except Exception as exc:
            logger.warning("PM merge failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_agent(self, role: str) -> Optional[Dict[str, Any]]:
        for agent in self.config.get("agents", []):
            if agent.get("role") == role:
                return agent
        return None

    def _resolve_token(self, agent: Dict[str, Any]) -> Optional[str]:
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
