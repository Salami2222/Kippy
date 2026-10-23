import json
import hashlib
import struct
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

    def test_release_endpoints_do_not_depend_on_ollama_and_identity_is_frozen(self):
        handler = object.__new__(AppHandler)
        handler.runtime_identity = {"commit": "a" * 40, "pid": 123, "started_at": "startup"}
        handler.app = Mock()
        handler.app.get_status.return_value = {"state": "idle"}
        handler.app.ollama.status.side_effect = RuntimeError("offline")
        handler._send = Mock()
        for route in ("/api/version", "/api/status"):
            handler.path = route
            with patch("macromellow.web.capture_runtime_identity", side_effect=AssertionError("must not re-read Git")):
                handler.do_GET()
            self.assertEqual(handler._send.call_args.args[0], 200)
        handler.app.ollama.status.assert_not_called()
        handler.path = "/api/version"
        handler.do_GET()
        self.assertEqual(json.loads(handler._send.call_args.args[1])["commit"], "a" * 40)

    def test_all_eight_mascot_assets_have_explicit_png_routes(self):
        for state in ("idle", "talking", "thinking", "profile"):
            for variant in ("", "-burned"):
                name = f"{state}{variant}.png"
                with self.subTest(name=name):
                    content = b"\x89PNG\r\n\x1a\n" + name.encode()
                    (self.assets / name).write_bytes(content)
                    self.assertEqual(self.get(f"/assets/mellow/{name}"),
                                     (200, content, "image/png"))

    def test_sleeping_v2_assets_use_explicit_png_routes(self):
        for name in ("idle-v2-atlas", "idle-v2-preview", *(f"idle-v2-frame-{n}" for n in range(1, 5))):
            content = b"PNG test " + name.encode()
            (self.assets / f"{name}.png").write_bytes(content)
            self.assertEqual(self.get(f"/assets/mellow/{name}.png"), (200, content, "image/png"))
        self.assertEqual(self.get("/assets/mellow/idle-v2-frame-5.png")[0], 404)

    def test_sleeping_v3_assets_use_allowlisted_routes(self):
        for name in ("mellow_sleeping_10frame_atlas", *(f"sleep_{n:02d}" for n in range(10))):
            (self.assets / f"{name}.png").write_bytes(b"PNG")
            self.assertEqual(self.get(f"/assets/mellow/{name}.png"), (200, b"PNG", "image/png"))
        (self.assets / "sleeping-v3-preview.gif").write_bytes(b"GIF89a")
        self.assertEqual(self.get("/assets/mellow/sleeping-v3-preview.gif"), (200, b"GIF89a", "image/gif"))
        self.assertEqual(self.get("/assets/mellow/sleep_10.png")[0], 404)

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


class SleepingV2AssetTests(unittest.TestCase):
    def test_shipped_frames_atlas_and_preview_have_consistent_dimensions(self):
        root = Path(__file__).resolve().parents[1] / "macromellow/static/assets/mellow"
        metadata = json.loads((root / "idle-v2.json").read_text())
        expected = (535, 377)
        for n in range(1, 5):
            data = (root / f"idle-v2-frame-{n}.png").read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), expected)
        atlas = (root / "idle-v2-atlas.png").read_bytes()
        self.assertEqual(struct.unpack(">II", atlas[16:24]), (expected[0] * 4, expected[1]))
        preview = (root / "idle-v2-preview.png").read_bytes()
        offset = 8
        controls = []
        while offset < len(preview):
            size = struct.unpack(">I", preview[offset:offset + 4])[0]
            kind = preview[offset + 4:offset + 8]
            if kind == b"acTL":
                self.assertEqual(struct.unpack(">II", preview[offset + 8:offset + 16]), (4, 0))
            if kind == b"fcTL":
                controls.append(preview[offset + 8:offset + 8 + size])
            offset += size + 12
        self.assertEqual(len(controls), 4)
        self.assertEqual([numerator / denominator for numerator, denominator in
                          (struct.unpack(">HH", control[20:24]) for control in controls)], [0.7] * 4)
        self.assertEqual(metadata["frame_size"], list(expected))
        self.assertEqual((metadata["rear_edge"], metadata["baseline"]), (6, 372))


class SleepingV3AssetTests(unittest.TestCase):
    def test_ten_original_frames_and_atlas_match_imported_hashes_and_dimensions(self):
        root = Path(__file__).resolve().parents[1] / "macromellow/static/assets/mellow"
        metadata = json.loads((root / "sleeping-v3.json").read_text())
        self.assertEqual((metadata["frame_count"], metadata["frame_ms"], metadata["cycle_ms"]), (10, 250, 2500))
        self.assertEqual(len(metadata["source_sha256"]), 11)
        for name, expected_hash in metadata["source_sha256"].items():
            data = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), expected_hash)
            dimensions = (5760, 450) if "atlas" in name else (576, 450)
            self.assertEqual(struct.unpack(">II", data[16:24]), dimensions)

    def test_implemented_gif_contains_ten_frames_at_250ms(self):
        root = Path(__file__).resolve().parents[1] / "macromellow/static/assets/mellow"
        data = (root / "sleeping-v3-preview.gif").read_bytes()
        self.assertEqual(data[:6], b"GIF89a")
        self.assertEqual(struct.unpack("<HH", data[6:10]), (576, 450))
        offset = 13 + (3 * (2 ** ((data[10] & 7) + 1)) if data[10] & 128 else 0)
        frames, delays = 0, []
        while offset < len(data):
            marker = data[offset]
            offset += 1
            if marker == 0x3B:
                break
            if marker == 0x21:
                label = data[offset]
                offset += 1
                if label == 0xF9:
                    delays.append(struct.unpack("<H", data[offset + 2:offset + 4])[0] * 10)
            elif marker == 0x2C:
                frames += 1
                packed = data[offset + 8]
                offset += 9
                if packed & 128:
                    offset += 3 * (2 ** ((packed & 7) + 1))
                offset += 1  # Minimum LZW code size.
            else:
                self.fail(f"Unexpected GIF marker {marker}")
            while data[offset]:
                offset += data[offset] + 1
            offset += 1
        self.assertEqual(frames, 10)
        self.assertEqual(delays, [250] * 10)


if __name__ == "__main__":
    unittest.main()
