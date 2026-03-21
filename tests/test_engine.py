import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from github_pm_agent.artifact_store import ArtifactStore
from github_pm_agent.engine import EventEngine
from github_pm_agent.models import Event


class FakeAIManager:
    def __init__(self, responses=None) -> None:
        self.requests = []
        self.responses = list(responses or [])

    def default_provider(self) -> str:
        return "fake"

    def default_model(self, provider_name: str = "") -> str:
        return "gpt-test"

    def generate(self, request):
        self.requests.append(request)
        content = self.responses.pop(0) if self.responses else json.dumps(
            {
                "should_act": False,
                "reason": "observe only",
                "action_type": "none",
                "target": {"kind": request.variables.get("event_type", "none"), "number": 0},
                "message": "",
                "labels_to_add": [],
                "labels_to_remove": [],
                "memory_note": "",
                "issue_title": "",
            }
        )
        return SimpleNamespace(
            provider=request.provider,
            model=request.model,
            content=content,
            session_key=request.session_key,
        )


class EventEngineMemoryRefTest(unittest.TestCase):
    def test_run_ai_handler_appends_distilled_memory_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime_dir = root / "runtime"
            distilled = runtime_dir / "memory" / "distilled.md"
            distilled.parent.mkdir(parents=True, exist_ok=True)
            distilled.write_text("# Distilled Memory\n\n- Keep prompts short.\n", encoding="utf-8")

            ai = FakeAIManager()
            engine = EventEngine(
                {"_project_root": str(root), "engine": {"supervisor_enabled": False}},
                ai,
                object(),
                runtime_dir,
            )

            result = engine.run_ai_handler(self._event(), prompt_path="prompts/task.md")

            self.assertEqual(
                ai.requests[0].memory_refs,
                ["memory/README.md", "runtime/memory/distilled.md"],
            )
            self.assertEqual(
                ai.requests[0].output_template_path,
                "templates/output/action_plan.json",
            )
            self.assertEqual(ai.requests[0].artifact_refs, [])
            self.assertFalse(result["plan"]["needs_human_decision"])
            self.assertEqual(result["plan"]["human_decision_reason"], "")
            self.assertEqual(result["plan"]["urgency"], "normal")
            self.assertEqual(result["plan"]["follow_up_after_hours"], 0)
            self.assertEqual(result["plan"]["evidence"], [])
            self.assertEqual(result["plan"]["options"], [])

    def test_run_ai_handler_attaches_existing_artifacts_and_persists_new_brief(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime_dir = root / "runtime"
            store = ArtifactStore(runtime_dir, project_root=root)
            record = store.save(
                "brief",
                title="Existing Brief",
                summary="current context",
                body="Existing brief body.",
                created_at="2026-03-19T00:00:00Z",
            )

            ai = FakeAIManager()
            engine = EventEngine(
                {"_project_root": str(root), "engine": {"supervisor_enabled": False}},
                ai,
                object(),
                runtime_dir,
            )

            result = engine.run_ai_handler(
                self._event(),
                prompt_path="prompts/actions/intake_clarify.md",
            )

            self.assertEqual(ai.requests[0].artifact_refs, [f"runtime/{record.path}"])
            self.assertEqual(result["artifact"]["kind"], "brief")
            self.assertIn("Assessment", store.latest_content("brief"))

    def test_run_ai_handler_can_attach_second_opinion_for_high_risk_pull_request(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime_dir = root / "runtime"
            ai = FakeAIManager(
                responses=[
                    json.dumps(
                        {
                            "should_act": False,
                            "reason": "primary",
                            "action_type": "none",
                            "target": {"kind": "pull_request", "number": 21},
                            "message": "",
                            "labels_to_add": [],
                            "labels_to_remove": [],
                            "action_input": {},
                            "memory_note": "",
                            "issue_title": "",
                        }
                    ),
                    json.dumps(
                        {
                            "should_act": False,
                            "reason": "second opinion",
                            "action_type": "none",
                            "target": {"kind": "pull_request", "number": 21},
                            "message": "",
                            "labels_to_add": [],
                            "labels_to_remove": [],
                            "action_input": {},
                            "memory_note": "",
                            "issue_title": "",
                            "needs_human_decision": True,
                            "human_decision_reason": "needs confirmation",
                        }
                    ),
                ]
            )
            engine = EventEngine(
                {
                    "_project_root": str(root),
                    "engine": {
                        "supervisor_enabled": False,
                        "second_opinion": {
                            "enabled": True,
                            "provider": "reviewer",
                            "model": "gpt-review",
                        },
                    },
                },
                ai,
                object(),
                runtime_dir,
            )

            event = self._event()
            event.event_type = "pull_request_changed"
            event.target_kind = "pull_request"
            event.target_number = 21
            result = engine.run_ai_handler(
                event,
                prompt_path="prompts/actions/review_readiness.md",
                risk_level="high",
            )

            self.assertEqual(len(ai.requests), 2)
            self.assertEqual(ai.requests[1].provider, "reviewer")
            self.assertEqual(ai.requests[1].prompt_path, "prompts/actions/second_opinion_review.md")
            self.assertTrue(result["second_opinion"]["plan"]["needs_human_decision"])


    def _event(self) -> Event:
        return Event(
            event_id="evt-mention",
            event_type="mention",
            source="test",
            occurred_at="2026-03-19T00:00:00Z",
            repo="songsjun/example",
            actor="alice",
            url="https://example.test",
            title="mention",
            body="@agent please help",
            target_kind="issue",
            target_number=12,
            metadata={},
        )


if __name__ == "__main__":
    unittest.main()
