import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from macromellow.umd import HALLS, save_json
from macromellow.web import DataApp, UNAVAILABLE_MESSAGE


def dataset(errors=None):
    return {
        "schema_version": 1,
        "source": "UMD Dining Services",
        "menu_date": "2026-10-19",
        "retrieved_at": "2026-10-19T19:00:00-04:00",
        "foods": [{"dining_hall": name, "name": "Test food", "calories": None}
                  for name, _ in HALLS.values()],
        "errors": errors or [],
    }


class WebDataTests(unittest.TestCase):
    def test_loads_saved_json_from_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "umd-2026-10-19.json"
            save_json(dataset(), path)
            loaded = DataApp(Path(directory)).datasets()
            self.assertEqual(len(loaded["datasets"]), 1)
            self.assertEqual(len(loaded["datasets"][0]["foods"]), 3)
            self.assertEqual(loaded["load_errors"], [])

    def test_uses_newest_retrieval_for_duplicate_date(self):
        with tempfile.TemporaryDirectory() as directory:
            newer = dataset()
            newer["retrieved_at"] = "2026-10-19T20:00:00-04:00"
            newer["foods"][0]["name"] = "Fresh menu"
            save_json(newer, Path(directory) / "newer.json")
            older = dataset()
            older["foods"][0]["name"] = "Old menu"
            save_json(older, Path(directory) / "later-written.json")
            loaded = DataApp(Path(directory)).datasets()
            self.assertEqual(loaded["datasets"][0]["foods"][0]["name"], "Fresh menu")

    def test_incomplete_refresh_keeps_existing_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "umd-2026-10-19.json"
            save_json(dataset(), path)
            original = path.read_bytes()
            app = DataApp(Path(directory))
            bad = dataset([{"dining_hall": "251 North", "error": "HTTP Error 429"}])
            with patch("macromellow.web.retrieve", return_value=bad):
                app._refresh(date(2026, 10, 19))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(app.get_status()["state"], "error")

    def test_official_unavailable_label_is_saved_as_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            app = DataApp(Path(directory))
            response = dataset([{"dining_hall": "251 North", "food": "Test food", "error": UNAVAILABLE_MESSAGE}])
            with patch("macromellow.web.retrieve", return_value=response):
                app._refresh(date(2026, 10, 19))
            saved = json.loads((Path(directory) / "umd-2026-10-19.json").read_text())
            self.assertIsNone(saved["foods"][0]["calories"])
            self.assertEqual(app.get_status()["state"], "warning")


if __name__ == "__main__":
    unittest.main()
