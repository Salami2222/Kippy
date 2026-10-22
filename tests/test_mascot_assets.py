import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from macromellow.web import AppHandler


class MascotStaticRoutesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.static_dir = patch("macromellow.web.STATIC_DIR", self.root)
        self.static_dir.start()
        self.addCleanup(self.static_dir.stop)
        self.assets = self.root / "assets" / "mellow"
        self.assets.mkdir(parents=True)

    def get(self, route):
        handler = object.__new__(AppHandler)
        handler.path = route
        handler._send = Mock()
        handler.do_GET()
        handler._send.assert_called_once()
        return handler._send.call_args.args

    def test_all_eight_mascot_assets_have_explicit_png_routes(self):
        for state in ("idle", "talking", "thinking", "profile"):
            for variant in ("", "-burned"):
                name = f"{state}{variant}.png"
                with self.subTest(name=name):
                    content = b"\x89PNG\r\n\x1a\n" + name.encode()
                    (self.assets / name).write_bytes(content)
                    self.assertEqual(self.get(f"/assets/mellow/{name}"),
                                     (200, content, "image/png"))

    def test_mascot_module_and_existing_interface_routes(self):
        for route, name, media_type in (
            ("/mascot.js", "mascot.js", "text/javascript; charset=utf-8"),
            ("/", "index.html", "text/html; charset=utf-8"),
            ("/app.css", "app.css", "text/css; charset=utf-8"),
            ("/app.js", "app.js", "text/javascript; charset=utf-8"),
            ("/tracker.js", "tracker.js", "text/javascript; charset=utf-8"),
        ):
            with self.subTest(route=route):
                content = f"Test contents of {name}".encode()
                (self.root / name).write_bytes(content)
                self.assertEqual(self.get(route), (200, content, media_type))

    def test_query_string_does_not_change_an_allowlisted_asset(self):
        (self.assets / "idle.png").write_bytes(b"sprite")
        self.assertEqual(self.get("/assets/mellow/idle.png?v=1"),
                         (200, b"sprite", "image/png"))

    def test_missing_assets_return_a_safe_error(self):
        for route in ("/assets/mellow/idle.png", "/mascot.js"):
            with self.subTest(route=route):
                status, body, media_type = self.get(route)
                self.assertEqual(status, 500)
                self.assertEqual(media_type, "application/json; charset=utf-8")
                self.assertEqual(json.loads(body), {"error": "Interface file is missing"})

    def test_unknown_or_encoded_paths_are_not_served(self):
        (self.assets / "idle.png").write_bytes(b"sprite")
        (self.assets / "secret.txt").write_bytes(b"private file")
        for route in (
            "/assets/mellow/secret.txt",
            "/assets/mellow/idle.svg",
            "/assets/mellow/walking.png",
            "/assets/mellow/IDLE.png",
            "/assets/mellow/../mellow/idle.png",
            "/assets/mellow/%2e%2e/mellow/idle.png",
            "/assets/mellow/%2e%2e%2fmellow%2fidle.png",
            "/assets/mellow/..\\mellow/idle.png",
            "/assets/mellow\\idle.png",
            "/assets/mellow%2fidle.png",
            "/assets/mellow/%69dle.png",
            "/assets/mellow/idle%2epng",
            "/assets/mellow/idle.png/..",
            "/assets/mellow/idle.png%00",
            "/assets/mellow/",
        ):
            with self.subTest(route=route):
                status, body, _ = self.get(route)
                self.assertEqual(status, 404)
                self.assertEqual(json.loads(body), {"error": "Not found"})

    def test_asset_symlink_cannot_escape_static_directory(self):
        with tempfile.TemporaryDirectory() as outside:
            private = Path(outside) / "private.png"
            private.write_bytes(b"private contents")
            (self.assets / "idle.png").symlink_to(private)
            status, body, _ = self.get("/assets/mellow/idle.png")
            self.assertEqual(status, 404)
            self.assertEqual(json.loads(body), {"error": "Not found"})


if __name__ == "__main__":
    unittest.main()
