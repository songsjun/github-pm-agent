from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from github_pm_agent.devenv_client import DevEnvClient, DevEnvError, TERMINAL_JOB_STATUSES
from github_pm_agent.utils import extract_json_object, git_auth_env

logger = logging.getLogger(__name__)

_GIT_USER_NAME = "github-pm-agent"
_GIT_USER_EMAIL = "github-pm-agent@local"
_CONTAINER_WORKDIR = "/workspace"
_PENDING_JOB_STATUSES = {"accepted", "building", "created", "pending", "pulling", "queued", "starting"}


@dataclass
class CodingPlan:
    files: list[dict[str, str]]
    test_command: str
    install_command: str
    branch_name: str
    commit_message: str


@dataclass
class TestResult:
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    summary: str


class CodingSession:
    MAX_ITERATIONS = 3

    def __init__(
        self,
        devenv_client: DevEnvClient,
        repo: str,
        issue_number: int,
        github_token: str = "",
        base_image: str = "python:3.12-slim",
    ) -> None:
        self.devenv_client = devenv_client
        self.repo = repo
        self.issue_number = issue_number
        self.github_token = github_token
        self.base_image = base_image
        self.workspace_id = f"issue-{repo.replace('/', '-')}-{issue_number}"
        self.work_dir = Path(tempfile.mkdtemp(prefix=self._temp_dir_prefix()))
        self.job_id: str | None = None
        self.iteration = 1
        self._branch_name: str | None = None
        self._plan_apply_count = 0

    def setup(self) -> None:
        """
        1. Create devenv workspace (idempotent: ok if already exists)
        2. Create temp work_dir and git clone the repo into it
           - Use a temporary git auth header when github_token is provided
           - Keep the stored remote URL token-free
        3. Set up git user config in work_dir for later commits
        """

        logger.info("Setting up coding session for %s#%s", self.repo, self.issue_number)
        if not self.work_dir.exists():
            self.work_dir = Path(tempfile.mkdtemp(prefix=self._temp_dir_prefix()))

        try:
            self.devenv_client.create_workspace(self.workspace_id)
            logger.info("Created DevEnv workspace %s", self.workspace_id)
        except DevEnvError as exc:
            if self._is_workspace_exists_error(exc):
                logger.info("DevEnv workspace %s already exists", self.workspace_id)
            else:
                raise RuntimeError(f"failed to create DevEnv workspace {self.workspace_id}: {exc}") from exc

        if not (self.work_dir / ".git").exists():
            logger.info("Cloning %s into %s", self.repo, self.work_dir)
            self._run_command(
                ["git", "clone", self._repo_clone_url(), str(self.work_dir)],
                env=self._git_command_env(),
            )

        self._run_command(["git", "config", "user.name", _GIT_USER_NAME], cwd=self.work_dir)
        self._run_command(["git", "config", "user.email", _GIT_USER_EMAIL], cwd=self.work_dir)

    def apply_plan(self, plan: CodingPlan) -> None:
        """
        1. Create branch: git checkout -b {plan.branch_name}
        2. Write all files in plan.files to work_dir (create parent dirs as needed)
        3. Stage all changes: git add -A
        4. Commit: git commit -m "{plan.commit_message}"
        """

        self._ensure_repo_ready()
        if self._plan_apply_count > 0:
            self.iteration += 1
            if self.iteration > self.MAX_ITERATIONS:
                raise RuntimeError(f"exceeded max coding iterations ({self.MAX_ITERATIONS}) for {self.repo}#{self.issue_number}")
        self._plan_apply_count += 1

        logger.info("Applying coding plan on branch %s (iteration %s)", plan.branch_name, self.iteration)
        self._checkout_branch(plan.branch_name)
        for file_spec in plan.files:
            destination = self._resolve_repo_path(file_spec["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(file_spec["content"], encoding="utf-8")

        self._run_command(["git", "add", "-A"], cwd=self.work_dir)
        diff_check = self._run_command(["git", "diff", "--cached", "--quiet"], cwd=self.work_dir, check=False)
        if diff_check.returncode == 0:
            raise RuntimeError("coding plan produced no staged changes to commit")
        if diff_check.returncode != 1:
            raise RuntimeError(f"failed to inspect staged diff for {self.work_dir}")

        self._run_command(["git", "commit", "-m", plan.commit_message], cwd=self.work_dir)
        self._branch_name = plan.branch_name

    def run_tests(self, plan: CodingPlan) -> TestResult:
        """
        Run tests by baking install + test commands into the Docker image build.
        Build logs capture full output; __TEST_EXIT_CODE__:<n> sentinel is parsed
        to determine pass/fail.  This avoids exec_in_job which is not reliable on
        all DevEnv server versions.

        1. Upload context archive (code + generated Dockerfile with RUN steps)
        2. Build image — RUN install, RUN test, echo __TEST_EXIT_CODE__:$?
        3. Wait for build job; collect build logs
        4. Parse exit code + output from logs → TestResult
        """

        self._ensure_repo_ready()
        if not plan.install_command.strip():
            raise RuntimeError("coding plan install_command is empty")
        if not plan.test_command.strip():
            raise RuntimeError("coding plan test_command is empty")

        logger.info("Running tests for %s#%s (build-time)", self.repo, self.issue_number)
        context_id: str | None = None
        build_job_id: str | None = None
        result: TestResult | None = None
        failure: Exception | None = None
        try:
            archive_bytes = self._build_context_archive(plan=plan)
            context_id = self.devenv_client.upload_context(archive_bytes, filename="context.tar.gz")
            image_tag = self._image_tag()

            build_job_id = self.devenv_client.build_image(
                context_id=context_id,
                tag=image_tag,
                workspace=self.workspace_id,
            )
            # Wait for build to reach terminal state (may succeed or fail)
            build_job: dict[str, Any] = {}
            try:
                build_job = self.devenv_client.wait_for_job(build_job_id)
            except DevEnvError:
                # Failed build is expected when tests fail; we'll parse logs below
                try:
                    build_job = self.devenv_client.inspect_job(build_job_id)
                except DevEnvError:
                    pass

            build_logs_text = self._get_job_logs(build_job_id or "")
            result = self._parse_build_test_result(build_logs_text, build_job)

        except DevEnvError as exc:
            failure = RuntimeError(f"failed to run tests in DevEnv workspace {self.workspace_id}: {exc}")
        except RuntimeError as exc:
            failure = exc

        cleanup_errors = self._cleanup_test_resources(build_job_id=build_job_id, context_id=context_id)
        if cleanup_errors:
            cleanup_error = RuntimeError("test resource cleanup failed: " + "; ".join(cleanup_errors))
            if failure is not None:
                raise RuntimeError(f"{failure}; additionally {cleanup_error}") from failure
            raise cleanup_error
        if failure is not None:
            raise failure
        if result is None:
            raise RuntimeError("test execution finished without a result")
        return result

    def fix_and_push(self, plan: CodingPlan) -> None:
        """
        Apply fix files to the EXISTING feature branch (fetched from origin) and push.
        Does NOT create a new PR — updates the existing one in-place.

        1. Fetch the remote branch
        2. Checkout locally (tracking remote)
        3. Write all files in plan.files
        4. Commit (if there are staged changes)
        5. Push to origin
        """
        self._ensure_repo_ready()
        branch_name = plan.branch_name
        logger.info("Applying fix on existing branch %s for %s#%s", branch_name, self.repo, self.issue_number)

        self._run_command(
            ["git", "fetch", "origin", self._remote_branch_refspec(branch_name)],
            cwd=self.work_dir,
            env=self._git_command_env(),
        )

        branch_exists_locally = (
            self._run_command(
                ["git", "rev-parse", "--verify", branch_name], cwd=self.work_dir, check=False
            ).returncode == 0
        )
        if branch_exists_locally:
            self._run_command(["git", "checkout", branch_name], cwd=self.work_dir)
            self._run_command(["git", "reset", "--hard", f"origin/{branch_name}"], cwd=self.work_dir)
        else:
            self._run_command(
                ["git", "checkout", "-b", branch_name, f"origin/{branch_name}"],
                cwd=self.work_dir,
            )

        for file_spec in plan.files:
            destination = self._resolve_repo_path(file_spec["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(file_spec["content"], encoding="utf-8")

        self._run_command(["git", "add", "-A"], cwd=self.work_dir)
        diff_check = self._run_command(
            ["git", "diff", "--cached", "--quiet"], cwd=self.work_dir, check=False
        )
        if diff_check.returncode == 1:  # staged changes exist
            self._run_command(["git", "commit", "-m", plan.commit_message], cwd=self.work_dir)

        self._branch_name = branch_name

        # Rebase on the latest main so the PR stays conflict-free for merge.
        logger.info("Rebasing fix branch %s on origin/main for %s#%s", branch_name, self.repo, self.issue_number)
        self._run_command(["git", "fetch", "origin", "main"], cwd=self.work_dir, env=self._git_command_env())
        rebase_result = self._run_command(
            ["git", "rebase", "origin/main"], cwd=self.work_dir, check=False
        )
        if rebase_result.returncode != 0:
            logger.warning("Rebase failed for fix branch %s — aborting and pushing as-is", branch_name)
            self._run_command(["git", "rebase", "--abort"], cwd=self.work_dir, check=False)
            self._run_command(["git", "push", "origin", branch_name], cwd=self.work_dir, env=self._git_command_env())
        else:
            self._run_command(
                ["git", "push", "origin", branch_name, "--force-with-lease"], cwd=self.work_dir, env=self._git_command_env()
            )

    def push_branch(self) -> str:
        """
        Rebase the branch on the latest origin/main, then push.
        Rebasing keeps the PR conflict-free so the PM can merge cleanly.
        Returns the branch name.
        """

        branch_name = self._current_branch_name()
        logger.info("Rebasing branch %s on origin/main for %s#%s", branch_name, self.repo, self.issue_number)
        self._run_command(["git", "fetch", "origin", "main"], cwd=self.work_dir, env=self._git_command_env())
        rebase_result = self._run_command(
            ["git", "rebase", "origin/main"], cwd=self.work_dir, check=False
        )
        if rebase_result.returncode != 0:
            logger.warning("Rebase failed for %s — aborting rebase and pushing as-is", branch_name)
            self._run_command(["git", "rebase", "--abort"], cwd=self.work_dir, check=False)
            self._run_command(["git", "push", "origin", branch_name], cwd=self.work_dir, env=self._git_command_env())
        else:
            # Force push after rebase to update the remote branch.
            self._run_command(
                ["git", "push", "origin", branch_name, "--force-with-lease"], cwd=self.work_dir, env=self._git_command_env()
            )
        return branch_name

    def create_pr(self, title: str, body: str, base_branch: str = "main") -> dict[str, Any]:
        """
        Use subprocess to call: gh pr create --title "..." --body "..." --base {base_branch}
        Returns dict with at least {"number": int, "url": str}
        Parse from gh output (JSON with --json number,url flag).
        Uses work_dir as cwd.
        """

        branch_name = self._current_branch_name()
        logger.info("Creating PR for branch %s against %s", branch_name, base_branch)
        create_result = self._run_command(
            [
                "gh",
                "pr",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--base",
                base_branch,
                "--head",
                branch_name,
            ],
            cwd=self.work_dir,
            env=self._command_env(),
        )
        pr_url = self._extract_pr_url(create_result.stdout) or self._extract_pr_url(create_result.stderr)
        if not pr_url:
            raise RuntimeError(f"gh pr create did not return a pull request URL: {create_result.stdout.strip() or create_result.stderr.strip()}")

        try:
            view_result = self._run_command(
                ["gh", "pr", "view", pr_url, "--json", "number,url"],
                cwd=self.work_dir,
                env=self._command_env(),
            )
            payload = json.loads(view_result.stdout)
        except (RuntimeError, json.JSONDecodeError):
            number = self._extract_pr_number(pr_url)
            if number is None:
                raise RuntimeError(f"unable to determine pull request number from gh output: {pr_url}")
            payload = {"number": number, "url": pr_url}

        if not isinstance(payload, dict):
            raise RuntimeError(f"gh pr view returned unexpected payload type: {type(payload).__name__}")
        if not isinstance(payload.get("number"), int) or not isinstance(payload.get("url"), str):
            raise RuntimeError(f"gh pr view payload missing required fields: {payload}")
        return {"number": payload["number"], "url": payload["url"]}

    def cleanup(self) -> None:
        """Remove work_dir (temp dir) and delete devenv workspace. Idempotent."""

        logger.info("Cleaning up coding session for %s#%s", self.repo, self.issue_number)
        errors: list[str] = []

        try:
            jobs = self.devenv_client.list_jobs(workspace=self.workspace_id)
        except DevEnvError as exc:
            if not self._is_not_found_error(exc):
                errors.append(f"list_jobs({self.workspace_id}): {exc}")
            jobs = []

        seen_job_ids = set()
        if self.job_id:
            seen_job_ids.add(self.job_id)
        for job in jobs:
            job_id = str(job.get("job_id") or job.get("id") or "").strip()
            if job_id:
                seen_job_ids.add(job_id)

        for job_id in seen_job_ids:
            try:
                self.devenv_client.stop_job(job_id)
            except DevEnvError as exc:
                if not self._is_not_found_error(exc):
                    errors.append(f"stop_job({job_id}): {exc}")
        self.job_id = None

        try:
            self.devenv_client.delete_workspace(self.workspace_id)
        except DevEnvError as exc:
            if not self._is_not_found_error(exc):
                errors.append(f"delete_workspace({self.workspace_id}): {exc}")

        if self.work_dir.exists():
            try:
                shutil.rmtree(self.work_dir)
            except OSError as exc:
                errors.append(f"remove {self.work_dir}: {exc}")

        if errors:
            raise RuntimeError("cleanup failed: " + "; ".join(errors))

    @staticmethod
    def parse_plan(ai_output: str) -> CodingPlan | None:
        """
        Extract CodingPlan from AI output.
        Try: find JSON block in ```json...``` or raw JSON in the text.
        Return None if parsing fails.
        """

        payload = extract_json_object(ai_output)
        if not isinstance(payload, dict):
            return None

        files = payload.get("files")
        test_command = payload.get("test_command")
        install_command = payload.get("install_command")
        branch_name = payload.get("branch_name")
        commit_message = payload.get("commit_message")

        if not isinstance(files, list):
            return None
        if not isinstance(test_command, str) or not test_command.strip():
            return None
        if not isinstance(install_command, str) or not install_command.strip():
            return None
        if not isinstance(branch_name, str) or not branch_name.strip():
            return None
        if not isinstance(commit_message, str) or not commit_message.strip():
            return None

        normalized_files: list[dict[str, str]] = []
        for item in files:
            if not isinstance(item, dict):
                return None
            path = item.get("path")
            content = item.get("content")
            if not isinstance(path, str) or not path.strip():
                return None
            if not isinstance(content, str):
                return None
            normalized_files.append({"path": path, "content": content})

        return CodingPlan(
            files=normalized_files,
            test_command=test_command.strip(),
            install_command=install_command.strip(),
            branch_name=branch_name.strip(),
            commit_message=commit_message.strip(),
        )

    def _checkout_branch(self, branch_name: str) -> None:
        current_branch = self._run_command(
            ["git", "branch", "--show-current"],
            cwd=self.work_dir,
        ).stdout.strip()
        if current_branch == branch_name:
            return

        branch_exists = self._run_command(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=self.work_dir,
            check=False,
        )
        if branch_exists.returncode == 0:
            self._run_command(["git", "checkout", branch_name], cwd=self.work_dir)
            return

        self._run_command(["git", "checkout", "-b", branch_name], cwd=self.work_dir)

    def _ensure_repo_ready(self) -> None:
        if not self.work_dir.exists():
            raise RuntimeError("coding session work_dir has been removed; call setup() again")
        if not (self.work_dir / ".git").exists():
            raise RuntimeError("coding session repository is not initialized; call setup() first")

    def _repo_clone_url(self) -> str:
        return f"https://github.com/{self.repo}.git"

    @staticmethod
    def _remote_branch_refspec(branch_name: str) -> str:
        return f"{branch_name}:refs/remotes/origin/{branch_name}"

    def _build_context_archive(self, plan: "CodingPlan | None" = None) -> bytes:
        if plan is not None:
            # Bake install + test into RUN steps so build logs capture full output.
            # The sentinel line lets us parse exit code without exec or artifact APIs.
            install_cmd = plan.install_command.replace("'", "'\\''")
            test_cmd = plan.test_command.replace("'", "'\\''")
            dockerfile = (
                f"FROM {self.base_image}\n"
                f"COPY . {_CONTAINER_WORKDIR}\n"
                f"WORKDIR {_CONTAINER_WORKDIR}\n"
                f"RUN {install_cmd}\n"
                f"RUN sh -c '{test_cmd}'; echo __TEST_EXIT_CODE__:$?\n"
            )
        else:
            dockerfile = f"FROM {self.base_image}\nCOPY . {_CONTAINER_WORKDIR}\nWORKDIR {_CONTAINER_WORKDIR}\n"
        _SKIP_NAMES = {".git", "Dockerfile"}

        def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
            # Drop .git and any pre-existing Dockerfile at the root; they will be
            # replaced by our generated Dockerfile below.
            name = Path(tarinfo.name).parts
            if name and name[-1] in _SKIP_NAMES:
                return None
            return tarinfo

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            archive.add(self.work_dir, arcname=".", filter=_filter)
            docker_bytes = dockerfile.encode("utf-8")
            docker_info = tarfile.TarInfo(name="Dockerfile")
            docker_info.size = len(docker_bytes)
            docker_info.mtime = int(time.time())
            archive.addfile(docker_info, io.BytesIO(docker_bytes))
        return buffer.getvalue()

    def _wait_for_container_ready(self, job_id: str, timeout: float = 60.0, poll_interval: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        last_status = ""
        while True:
            job = self.devenv_client.inspect_job(job_id)
            status = str(job.get("status", "")).strip().lower()
            last_status = status or last_status
            if status in TERMINAL_JOB_STATUSES:
                error_text = str(job.get("error", "")).strip()
                suffix = f": {error_text}" if error_text else ""
                raise RuntimeError(f"container job {job_id} ended before tests ran with status {status}{suffix}")
            if job.get("container_id") or job.get("containerId"):
                return
            if status and status not in _PENDING_JOB_STATUSES:
                logger.info("Container job %s reached status %s; treating as ready", job_id, status)
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"timed out waiting for container job {job_id} to become ready (last status: {last_status or 'unknown'})")
            time.sleep(poll_interval)

    def _exec_shell_command(self, job_id: str, command: str) -> dict[str, Any]:
        try:
            payload = self.devenv_client.exec_in_job(
                job_id=job_id,
                command=["sh", "-lc", command],
                workdir=_CONTAINER_WORKDIR,
            )
        except DevEnvError as exc:
            raise RuntimeError(f"failed to execute command in container {job_id}: {command!r}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected exec payload type for command {command!r}: {type(payload).__name__}")
        return payload

    def _parse_exec_result(self, payload: dict[str, Any]) -> tuple[int, str, str]:
        source = payload
        if isinstance(payload.get("result"), dict):
            source = payload["result"]
        exit_code = self._first_int(
            source.get("exit_code"),
            source.get("exitCode"),
            source.get("returncode"),
            source.get("code"),
            0,
        )
        stdout = self._first_text(
            source.get("stdout"),
            source.get("output"),
            source.get("logs"),
            "",
        )
        stderr = self._first_text(
            source.get("stderr"),
            source.get("error"),
            "",
        )
        return exit_code, stdout, stderr

    def _parse_build_test_result(self, build_logs: str, build_job: dict[str, Any]) -> TestResult:
        """Parse test results from Docker build logs captured during a build-time test run."""
        sentinel_pattern = re.compile(r"__TEST_EXIT_CODE__:(\d+)")

        # BuildKit prefixes log lines with metadata like "#9 2.746", so match
        # the sentinel anywhere in the line instead of only at the start.
        exit_code = 1  # default to failure
        for line in reversed(build_logs.splitlines()):
            match = sentinel_pattern.search(line)
            if match:
                try:
                    exit_code = int(match.group(1))
                    break
                except ValueError:
                    pass

        # Extract lines after the last test RUN step and before the sentinel.
        # Support both classic "Step 4/4" logs and BuildKit "[5/5] RUN" logs.
        test_output_lines: list[str] = []
        lines = build_logs.splitlines()
        run_step_index = None
        for index, line in enumerate(lines):
            if "RUN sh -c" in line:
                run_step_index = index

        if run_step_index is not None:
            for line in lines[run_step_index + 1 :]:
                if sentinel_pattern.search(line):
                    break
                test_output_lines.append(line)

        # If the test step was never reached (e.g. install failed), fall back to all build logs
        if run_step_index is None:
            test_output_lines = lines
        stdout = "\n".join(test_output_lines).strip()
        passed = exit_code == 0
        status_label = "PASSED" if passed else "FAILED"
        summary = f"Tests {status_label} (exit code {exit_code}).\n\n{stdout[:2000]}" if stdout else f"Tests {status_label} (exit code {exit_code})."
        return TestResult(
            passed=passed,
            exit_code=exit_code,
            stdout=stdout,
            stderr="",
            summary=summary,
        )

    def _cleanup_test_resources(self, *, build_job_id: str | None, context_id: str | None) -> list[str]:
        errors: list[str] = []
        job_ids = [job_id for job_id in [self.job_id, build_job_id] if job_id]
        for job_id in job_ids:
            try:
                self.devenv_client.stop_job(job_id)
            except DevEnvError as exc:
                if not self._is_not_found_error(exc):
                    errors.append(f"stop_job({job_id}): {exc}")
        self.job_id = None

        if context_id:
            try:
                self.devenv_client.delete_context(context_id)
            except DevEnvError as exc:
                if not self._is_not_found_error(exc):
                    errors.append(f"delete_context({context_id}): {exc}")
        return errors

    def _resolve_repo_path(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise RuntimeError(f"refusing to write absolute path outside repository: {relative_path}")
        destination = (self.work_dir / relative).resolve()
        repo_root = self.work_dir.resolve()
        try:
            destination.relative_to(repo_root)
        except ValueError as exc:
            raise RuntimeError(f"refusing to write path outside repository: {relative_path}") from exc
        return destination

    def _current_branch_name(self) -> str:
        if self._branch_name:
            return self._branch_name
        branch_name = self._run_command(["git", "branch", "--show-current"], cwd=self.work_dir).stdout.strip()
        if not branch_name:
            raise RuntimeError("unable to determine current git branch")
        self._branch_name = branch_name
        return branch_name

    def _run_command(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                cwd=str(cwd) if cwd else None,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise RuntimeError(f"failed to execute {' '.join(command)}: {exc}") from exc
        if check and result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "no output"
            raise RuntimeError(
                f"command {' '.join(command)!r} failed with exit code {result.returncode}: {details}"
            )
        return result

    def _command_env(self) -> dict[str, str] | None:
        if not self.github_token:
            return None
        env = dict(os.environ)
        env["GITHUB_TOKEN"] = self.github_token
        return env

    def _git_command_env(self) -> dict[str, str] | None:
        if not self.github_token:
            return None
        return git_auth_env(self.github_token, base_env=os.environ)

    def _image_tag(self) -> str:
        safe_workspace = re.sub(r"[^a-z0-9_.-]+", "-", self.workspace_id.lower()).strip("-") or "workspace"
        return f"coding-session-{safe_workspace}:{self.iteration}-{int(time.time())}"

    def _get_job_logs(self, job_id: str) -> str:
        try:
            raw = self.devenv_client.get_logs(job_id)
        except DevEnvError:
            return ""
        # DevEnv may return {"job_id": ..., "lines": [...]} instead of plain text
        if raw.strip().startswith("{"):
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict) and isinstance(payload.get("lines"), list):
                    return "\n".join(str(line) for line in payload["lines"])
            except (json.JSONDecodeError, ValueError):
                pass
        return raw

    def _temp_dir_prefix(self) -> str:
        safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.repo).strip("-") or "repo"
        return f"coding-session-{safe_repo}-{self.issue_number}-"

    @staticmethod
    def _extract_pr_url(output: str) -> str | None:
        match = re.search(r"https://github\.com/\S+/pull/\d+", output)
        return match.group(0) if match else None

    @staticmethod
    def _extract_pr_number(url: str) -> int | None:
        match = re.search(r"/pull/(\d+)(?:$|[/?#])", url)
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _summarize_command_result(label: str, exit_code: int, stdout: str, stderr: str) -> str:
        outcome = "passed" if exit_code == 0 and label == "tests" else ("succeeded" if exit_code == 0 else "failed")
        first_line = CodingSession._first_nonempty_line(stderr) or CodingSession._first_nonempty_line(stdout)
        if first_line:
            return f"{label.capitalize()} {outcome} (exit {exit_code}): {first_line}"
        return f"{label.capitalize()} {outcome} (exit {exit_code})"

    @staticmethod
    def _first_nonempty_line(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @staticmethod
    def _first_int(*values: Any) -> int:
        for value in values:
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip():
                try:
                    return int(value.strip())
                except ValueError:
                    continue
        return 0

    @staticmethod
    def _first_text(*values: Any) -> str:
        for value in values:
            if value is None:
                continue
            if isinstance(value, str):
                return value
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            if value != "":
                return str(value)
        return ""

    @staticmethod
    def _is_workspace_exists_error(exc: DevEnvError) -> bool:
        message = str(exc).lower()
        return "status 409" in message or "already exists" in message

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "status 404" in message or "not found" in message or "no such file or directory" in message
