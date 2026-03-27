from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from github_pm_agent.project_release_scanner import ProjectReleaseScanner
from github_pm_agent.queue_store import QueueStore
from github_pm_agent.workflow_instance import WorkflowInstance


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.created_issues = []

    def api(self, path, params=None, method="GET"):
        state = None if not isinstance(params, dict) else params.get("state")
        if (path, state) in self.responses:
            return self.responses[(path, state)]
        return self.responses.get(path, [])

    def create_issue(self, title, body, labels=None):
        number = 900 + len(self.created_issues) + 1
        payload = {"number": number, "title": title, "body": body, "labels": list(labels or [])}
        self.created_issues.append(payload)
        return payload


class ProjectReleaseScannerTest(unittest.TestCase):
    def _readme_payload(self, text: str) -> dict:
        return {"content": base64.b64encode(text.encode("utf-8")).decode("ascii")}

    def test_enqueues_release_when_project_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            queue = QueueStore(runtime_dir)

            discussion = WorkflowInstance.load(runtime_dir, "acme/widgets", 1)
            discussion.set_workflow_type("discussion")
            discussion.set_completed()

            issue = WorkflowInstance.load(runtime_dir, "acme/widgets", 3)
            issue.set_workflow_type("issue_coding")
            issue.set_completed()

            client = FakeClient(
                {
                    "repos/acme/widgets/issues": [],
                    ("repos/acme/widgets/pulls", "open"): [],
                    ("repos/acme/widgets/pulls", "closed"): [
                        {
                            "number": 12,
                            "title": "feat: ship widget",
                            "merged_at": "2026-03-26T10:00:00Z",
                            "updated_at": "2026-03-26T10:00:00Z",
                        }
                    ],
                    "repos/acme/widgets/releases": [],
                    "repos/acme/widgets/readme": self._readme_payload(
                        "# Demo\n\n"
                        "## Overview\nProject overview.\n\n"
                        "## Install\nInstall steps.\n\n"
                        "## Run\nRun steps.\n\n"
                        "## Deployment\nDeploy steps.\n"
                    ),
                }
            )

            scanner = ProjectReleaseScanner(
                queue,
                {"acme/widgets": client},
                {"github": {"default_branch": "main"}},
            )

            result = scanner.scan_and_enqueue()

            self.assertEqual(len(result), 1)
            event = queue.pop()
            assert event is not None
            self.assertEqual(event.event_type, "project_release_ready")
            self.assertEqual(event.metadata["tag_name"], "v0.1.0")
            self.assertEqual(event.metadata["merged_pr_count"], 1)

    def test_skips_release_when_open_business_issue_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            queue = QueueStore(runtime_dir)

            discussion = WorkflowInstance.load(runtime_dir, "acme/widgets", 1)
            discussion.set_workflow_type("discussion")
            discussion.set_completed()

            issue = WorkflowInstance.load(runtime_dir, "acme/widgets", 3)
            issue.set_workflow_type("issue_coding")
            issue.set_completed()

            client = FakeClient(
                {
                    "repos/acme/widgets/issues": [
                        {
                            "number": 99,
                            "title": "Still open",
                            "labels": [{"name": "frontend"}],
                        }
                    ],
                    ("repos/acme/widgets/pulls", "open"): [],
                    ("repos/acme/widgets/pulls", "closed"): [],
                    "repos/acme/widgets/releases": [],
                    "repos/acme/widgets/readme": self._readme_payload(
                        "# Demo\n\n## Overview\nx\n\n## Install\nx\n\n## Run\nx\n\n## Deployment\nx\n"
                    ),
                }
            )

            scanner = ProjectReleaseScanner(
                queue,
                {"acme/widgets": client},
                {"github": {"default_branch": "main"}},
            )

            result = scanner.scan_and_enqueue()

            self.assertEqual(result, [])
            self.assertIsNone(queue.pop())

    def test_skips_release_when_readme_lacks_deployment_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            queue = QueueStore(runtime_dir)

            discussion = WorkflowInstance.load(runtime_dir, "acme/widgets", 1)
            discussion.set_workflow_type("discussion")
            discussion.set_completed()

            issue = WorkflowInstance.load(runtime_dir, "acme/widgets", 3)
            issue.set_workflow_type("issue_coding")
            issue.set_completed()

            client = FakeClient(
                {
                    "repos/acme/widgets/issues": [],
                    ("repos/acme/widgets/pulls", "open"): [],
                    ("repos/acme/widgets/pulls", "closed"): [
                        {
                            "number": 12,
                            "title": "feat: ship widget",
                            "merged_at": "2026-03-26T10:00:00Z",
                            "updated_at": "2026-03-26T10:00:00Z",
                        }
                    ],
                    "repos/acme/widgets/releases": [],
                    "repos/acme/widgets/readme": self._readme_payload(
                        "# Demo\n\n"
                        "## Overview\nProject overview.\n\n"
                        "## Install\nInstall steps.\n\n"
                        "## Run\nRun steps.\n"
                    ),
                }
            )

            scanner = ProjectReleaseScanner(
                queue,
                {"acme/widgets": client},
                {"github": {"default_branch": "main"}},
            )

            result = scanner.scan_and_enqueue()

            self.assertEqual(
                result,
                [
                    {
                        "repo": "acme/widgets",
                        "blocked_reason": "missing_readme_sections",
                        "missing_sections": ["deployment"],
                        "created_issue_number": 901,
                    }
                ],
            )
            self.assertEqual(len(client.created_issues), 1)
            self.assertEqual(client.created_issues[0]["title"], ProjectReleaseScanner.README_ISSUE_TITLE)
            self.assertEqual(client.created_issues[0]["labels"], ["ready-to-code"])
            self.assertIsNone(queue.pop())

    def test_skips_release_when_standalone_app_is_not_runnable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            queue = QueueStore(runtime_dir)

            discussion = WorkflowInstance.load(runtime_dir, "acme/widgets", 1)
            discussion.set_workflow_type("discussion")
            discussion.set_artifact(
                "project_context_contract",
                '{"delivery_type":"standalone_website","requires_runnable_app":true,"required_capabilities":["local_run"]}',
            )
            discussion.set_completed()

            issue = WorkflowInstance.load(runtime_dir, "acme/widgets", 3)
            issue.set_workflow_type("issue_coding")
            issue.set_completed()

            client = FakeClient(
                {
                    "repos/acme/widgets/issues": [],
                    ("repos/acme/widgets/pulls", "open"): [],
                    ("repos/acme/widgets/pulls", "closed"): [
                        {
                            "number": 12,
                            "title": "feat: ship widget",
                            "merged_at": "2026-03-26T10:00:00Z",
                            "updated_at": "2026-03-26T10:00:00Z",
                        }
                    ],
                    "repos/acme/widgets/releases": [],
                    "repos/acme/widgets/readme": self._readme_payload(
                        "# Demo\n\n## Overview\nProject overview.\n\n## Install\nInstall steps.\n\n## Run\nRun steps.\n\n## Deployment\nDeploy steps.\n"
                    ),
                    "repos/acme/widgets/contents/package.json": self._readme_payload(
                        '{"name":"widgets","scripts":{"test":"jest"}}'
                    ),
                }
            )

            scanner = ProjectReleaseScanner(
                queue,
                {"acme/widgets": client},
                {"github": {"default_branch": "main"}},
            )

            result = scanner.scan_and_enqueue()

            self.assertEqual(
                result,
                [
                    {
                        "repo": "acme/widgets",
                        "blocked_reason": "missing_runnable_app_files",
                        "missing_files": ["index.html", "src/main.* or src/index.*", "src/App.*"],
                        "missing_scripts": ["dev/build/start"],
                        "created_issue_number": 901,
                    }
                ],
            )
            self.assertEqual(client.created_issues[0]["title"], ProjectReleaseScanner.RUNNABLE_APP_ISSUE_TITLE)
            self.assertIsNone(queue.pop())


if __name__ == "__main__":
    unittest.main()
