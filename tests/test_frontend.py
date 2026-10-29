"""Static checks on the browser code (no JS runtime is available in this project)."""

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "todo_app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")


class FrontendConsistencyTests(unittest.TestCase):
    def test_every_referenced_id_exists(self):
        html_ids = set(re.findall(r'\bid="([^"]+)"', HTML))
        js_ids = set(re.findall(r'byId\("([^"]+)"\)', JS))
        self.assertTrue(js_ids, "app.js should look up elements with byId")
        self.assertEqual(js_ids - html_ids, set())

    def test_ids_are_unique(self):
        ids = re.findall(r'\bid="([^"]+)"', HTML)
        self.assertEqual(len(ids), len(set(ids)))

    def test_only_local_resources(self):
        sources = re.findall(r'(?:src|href)="([^"]+)"', HTML)
        self.assertTrue(sources)
        for src in sources:
            with self.subTest(src=src):
                self.assertTrue(src.startswith("/") and not src.startswith("//"), src)
        self.assertNotIn("<script>", HTML, "inline scripts would be blocked by the CSP")

    def test_api_paths_used_by_js_exist(self):
        from todo_app.server import App
        from todo_app.store import Store

        app = App(Store(":memory:"))
        examples = {
            "/api/today": "GET",
            "/api/calendar": "GET",
            "/api/tasks": "POST",
            "/api/tasks/overdue": "GET",
            "/api/tasks/1": "PATCH",
            "/api/journal/2026-10-29": "PUT",
        }
        for path, method in examples.items():
            with self.subTest(path=path):
                self.assertIn(path.split("/1")[0].split("/2026")[0], JS)
                app.resolve(method, path)  # raises if the route is missing


class XssTests(unittest.TestCase):
    def test_no_html_string_sinks(self):
        for sink in ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                     "new Function", "setTimeout(\"", "javascript:"]:
            with self.subTest(sink=sink):
                self.assertNotIn(sink, JS)

    def test_el_helper_does_not_set_html(self):
        # Event handlers are attached with addEventListener, not via on* attributes.
        self.assertIn('key.startsWith("on")) node.addEventListener', JS)


if __name__ == "__main__":
    unittest.main()
