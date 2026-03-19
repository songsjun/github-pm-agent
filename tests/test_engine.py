import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from github_pm_agent.engine import EventEngine
from github_pm_agent.models import Event


class FakeAIManager:
    def __init__(self) -> None:
        self.requests = []

    def default_provider(self) -> str:
        return "fake"

    def default_model(self, provider_name: str = "") -> str:
        return "gpt-test"

    def generate(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            provider=request.provider,
            model=request.model,
            content=json.dumps(
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
            ),
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

            engine.run_ai_handler(self._event(), prompt_path="prompts/task.md")

            self.assertEqual(
                ai.requests[0].memory_refs,
                ["memory/README.md", "runtime/memory/distilled.md"],
            )

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
