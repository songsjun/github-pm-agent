import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import patch

from github_pm_agent.app import GitHubPMAgentApp
from github_pm_agent.config import load_config, repo_name, runtime_dir
from github_pm_agent.github_client import GitHubClient
from github_pm_agent.models import Event
from github_pm_agent.workflow_orchestrator import WorkflowOrchestrator


class GitHubClientTokenEnvTest(unittest.TestCase):
    def test_run_uses_agent_specific_token_env_when_present(self) -> None:
        client = GitHubClient("gh", "songsjun/example", token_env="GITHUB_TOKEN_ENGINEER")
        completed = SimpleNamespace(stdout="ok\n")

        with patch.dict(os.environ, {"GITHUB_TOKEN_ENGINEER": "secret-token"}, clear=False):
            with patch("github_pm_agent.github_client.subprocess.run", return_value=completed) as run_mock:
                output = client._run(["api", "rate_limit"])

        self.assertEqual(output, "ok")
        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs["env"]["GITHUB_TOKEN"], "secret-token")

    def test_run_falls_back_to_default_env_when_agent_token_missing(self) -> None:
        client = GitHubClient("gh", "songsjun/example", token_env="GITHUB_TOKEN_PM")
        completed = SimpleNamespace(stdout="ok\n")

        with patch.dict(os.environ, {}, clear=True):
            with patch("github_pm_agent.github_client.subprocess.run", return_value=completed) as run_mock:
                client._run(["api", "rate_limit"])

        _, kwargs = run_mock.call_args
        self.assertNotIn("env", kwargs)


class WorkflowOrchestratorAgentConfigTest(unittest.TestCase):
    def test_build_participants_prefers_agent_config_and_merges_conditions(self) -> None:
        orchestrator = WorkflowOrchestrator(
            Path.cwd(),
            _FakeEngine(_NamedActions("default")),
            _NamedActions("default"),
            _FakeClient(),
            {},
            agent_configs=[
                {
                    "id": "pm",
                    "role": "pm",
                    "priority": 2,
                    "participates_in": {"pull_request_changed": "observe"},
                },
                {
                    "id": "security",
                    "role": "security",
                    "priority": 3,
                    "participates_in": {"pull_request_changed": "veto"},
                },
                {
                    "id": "engineer",
                    "role": "engineer",
                    "priority": 1,
                    "participates_in": {"pull_request_changed": "respond"},
                },
            ],
        )

        participants = orchestrator._build_participants(
            "pull_request_changed",
            {
                "participants": [{"role": "ignored", "priority": 99}],
                "conditions_by_role": {"security": {"files_match": "auth/**"}},
            },
        )

        self.assertEqual([participant["id"] for participant in participants], ["engineer", "pm", "security"])
        self.assertEqual(participants[2]["condition"], {"files_match": "auth/**"})

    def test_build_participants_falls_back_to_workflow_yaml_without_agent_config(self) -> None:
        orchestrator = WorkflowOrchestrator(
            Path.cwd(),
            _FakeEngine(_NamedActions("default")),
            _NamedActions("default"),
            _FakeClient(),
            {},
        )

        participants = orchestrator._build_participants(
            "pull_request_changed",
            {
                "participants": [
                    {"role": "pm", "priority": 2},
                    {"role": "engineer", "priority": 1},
                ]
            },
        )

        self.assertEqual([participant["role"] for participant in participants], ["engineer", "pm"])

    def test_execute_participant_uses_agent_specific_toolkit_before_permission_wrapper(self) -> None:
        default_actions = _NamedActions("default")
        agent_actions = _NamedActions("engineer-toolkit")
        engine = _FakeEngine(default_actions)
        engine.role_registry = _RoleRegistry({"engineer": {"permissions": {"allowed": ["comment"]}}})
        orchestrator = WorkflowOrchestrator(
            Path.cwd(),
            engine,
            default_actions,
            _FakeClient(),
            {},
            agent_configs=[{"id": "engineer", "role": "engineer", "participates_in": {"pull_request_changed": "respond"}}],
            agent_toolkits={"engineer": agent_actions},
        )

        result = orchestrator._execute_participant(
            _event(),
            {"id": "engineer", "role": "engineer", "action_mode": "respond", "priority": 1},
        )

        self.assertEqual(result["action"]["raw"]["origin"], "engineer-toolkit")
        self.assertEqual(default_actions.comment_calls, 0)
        self.assertEqual(agent_actions.comment_calls, 1)
        self.assertIs(engine.actions, default_actions)


class GitHubPMAgentAppAgentToolkitTest(unittest.TestCase):
    def test_app_builds_per_agent_toolkits(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tempdir:
            config = {
                "_project_root": tempdir,
                "github": {"repo": "songsjun/example", "owner": "songsjun"},
                "engine": {"dry_run": True},
                "agents": [
                    {
                        "id": "engineer",
                        "role": "engineer",
                        "token_env": "GITHUB_TOKEN_ENGINEER",
                        "participates_in": {"pull_request_changed": "respond"},
                    },
                    {
                        "id": "pm",
                        "role": "pm",
                        "token_env": "GITHUB_TOKEN_PM",
                        "participates_in": {"pull_request_changed": "observe"},
                    },
                ],
                "ai": {"default_provider": "codex", "providers": {"codex": {"type": "cli_script", "script": "scripts/ai_provider.py", "provider_name": "codex", "default_model": "codex-1"}}},
            }

            app = GitHubPMAgentApp(config, repo_root)

        self.assertEqual([agent["id"] for agent in app.orchestrator.agent_configs], ["engineer", "pm"])
        self.assertEqual(app.orchestrator.agent_toolkits["engineer"].client.token_env, "GITHUB_TOKEN_ENGINEER")
        self.assertEqual(app.orchestrator.agent_toolkits["pm"].client.token_env, "GITHUB_TOKEN_PM")


class ConfigCompatibilityTest(unittest.TestCase):
    def test_yaml_config_supports_repos_and_runtime_dir_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "config.example.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "github:",
                        "  repos:",
                        "    - your-org/your-repo",
                        "runtime_dir: .runtime",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_config(str(config_path))

        self.assertEqual(repo_name(config), "your-org/your-repo")
        self.assertEqual(runtime_dir(config).name, ".runtime")


class _NamedActions:
    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.comment_calls = 0

    def comment(self, target_kind: str, target_number: Optional[int], message: str) -> Dict[str, Any]:
        self.comment_calls += 1
        return {
            "origin": self.origin,
            "target_kind": target_kind,
            "target_number": target_number,
            "message": message,
        }


class _FakeEngine:
    def __init__(self, actions: _NamedActions) -> None:
        self.actions = actions
        self.role_registry = None

    def process(self, event: Event) -> Dict[str, Any]:
        raw = self.actions.comment(event.target_kind, event.target_number, "reply")
        return {"action": {"executed": True, "raw": raw}}

    def run_ai_handler(self, *args: object, **kwargs: object) -> Dict[str, Any]:
        return {}

    def run_veto_handler(self, event: Event, role: str = "pm") -> Dict[str, Any]:
        return {"vetoed": False, "veto_reason": "", "action": {"executed": False, "raw": {}}}


class _RoleRegistry:
    def __init__(self, roles: Dict[str, Dict[str, Any]]) -> None:
        self.roles = roles

    def load(self, role: str) -> Dict[str, Any]:
        return self.roles.get(role, {})


class _FakeClient:
    def api(self, path: str, params: Optional[Dict[str, Any]] = None, method: str = "GET") -> Any:
        return []


def _event() -> Event:
    return Event(
        event_id="evt-1",
        event_type="pull_request_changed",
        source="test",
        occurred_at="2026-03-20T00:00:00Z",
        repo="songsjun/example",
        actor="alice",
        url="https://example.test/pr/1",
        title="PR",
        body="body",
        target_kind="pull_request",
        target_number=1,
        metadata={},
    )


if __name__ == "__main__":
    unittest.main()
