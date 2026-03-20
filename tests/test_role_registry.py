from pathlib import Path

from github_pm_agent.role_registry import RoleRegistry


def test_role_registry_loads_pm_permissions() -> None:
    project_root = Path(__file__).resolve().parent.parent
    registry = RoleRegistry(project_root)

    config = registry.load("pm")

    assert "role" in config
    assert "permissions" in config
    assert "system_prompt_path" in config
    assert "skill_refs" in config
    assert "comment" in config["permissions"]["allowed"]
    assert "merge" in config["permissions"]["forbidden"]
    assert registry.exists("pm") is True
    assert registry.exists("nonexistent") is False
