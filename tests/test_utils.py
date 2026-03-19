import unittest

from github_pm_agent.utils import extract_json_object


class JsonExtractTest(unittest.TestCase):
    def test_extract_direct_json(self) -> None:
        payload = extract_json_object('{"ok": true}')
        self.assertEqual(payload, {"ok": True})

    def test_extract_json_from_fenced_block(self) -> None:
        payload = extract_json_object("before\n```json\n{\"ok\":true}\n```\nafter")
        self.assertEqual(payload, {"ok": True})


if __name__ == "__main__":
    unittest.main()
