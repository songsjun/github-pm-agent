from __future__ import annotations

import tempfile
from pathlib import Path

from github_pm_agent.workflow_instance import WorkflowInstance


def test_set_phase_resets_loop_counters_for_previous_phase() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 3)

        instance.set_phase("pm_decision")
        instance.increment_gate_open_count("pm_decision")
        instance.increment_gate_open_count("pm_decision")
        instance.increment_clarification_round("pm_decision")

        assert instance.get_gate_open_count("pm_decision") == 2
        assert instance.get_clarification_round("pm_decision") == 1

        instance.set_phase("merge_conflict_resolution")

        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 3)
        assert reloaded.get_gate_open_count("pm_decision") == 0
        assert reloaded.get_clarification_round("pm_decision") == 0
        assert reloaded.get_phase() == "merge_conflict_resolution"


def test_set_phase_keeps_loop_counters_when_phase_is_unchanged() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir)
        instance = WorkflowInstance.load(runtime_dir, "songsjun/example", 3)

        instance.set_phase("pm_decision")
        instance.increment_gate_open_count("pm_decision")

        instance.set_phase("pm_decision")

        reloaded = WorkflowInstance.load(runtime_dir, "songsjun/example", 3)
        assert reloaded.get_gate_open_count("pm_decision") == 1
        assert reloaded.get_phase() == "pm_decision"
