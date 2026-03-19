import tempfile
import unittest
from pathlib import Path

from github_pm_agent.artifact_store import ARTIFACT_KINDS, ArtifactStore


class ArtifactStoreTest(unittest.TestCase):
    def test_save_and_latest_reflects_most_recent_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime_dir = root / "runtime"
            store = ArtifactStore(runtime_dir, project_root=root)

            first = store.save(
                "brief",
                body="First brief body.",
                title="Project Brief",
                summary="Initial scope",
                created_at="2026-03-19T00:00:00Z",
            )
            second = store.save(
                "brief",
                body="Second brief body.",
                title="Updated Brief",
                summary="Refined scope",
                created_at="2026-03-19T01:00:00Z",
            )

            latest = store.latest("brief")

            self.assertIsNotNone(latest)
            self.assertEqual(latest.title, "Updated Brief")
            self.assertEqual(latest.summary, "Refined scope")
            self.assertIn("Second brief body.", store.read(latest))
            self.assertTrue((runtime_dir / second.path).exists())
            self.assertEqual(store.latest_refs(["brief"]), [f"runtime/{second.path}"])
            self.assertNotEqual(first.path, second.path)

    def test_unsupported_artifact_kind_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            store = ArtifactStore(root / "runtime", project_root=root)

            with self.assertRaises(ValueError):
                store.save("unknown", body="body")

    def test_supported_artifact_kinds_are_fixed(self) -> None:
        self.assertEqual(
            tuple(ARTIFACT_KINDS),
            ("brief", "spec_review", "release_readiness", "retro_summary"),
        )

    def test_save_dedupes_filename_when_timestamp_and_title_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            store = ArtifactStore(root / "runtime", project_root=root)

            first = store.save(
                "brief",
                body="body 1",
                title="Repeated Brief",
                created_at="2026-03-19T00:00:00Z",
            )
            second = store.save(
                "brief",
                body="body 2",
                title="Repeated Brief",
                created_at="2026-03-19T00:00:00Z",
            )

            self.assertNotEqual(first.path, second.path)
            self.assertTrue(second.path.endswith("-2.md"))


if __name__ == "__main__":
    unittest.main()
