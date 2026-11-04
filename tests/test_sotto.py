"""sotto's checks, on Linux, macOS and Windows: `uv run python -m unittest discover -s tests -v`."""
import json
import os
import re
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402,F401  (sets up temp dirs and demo mode before sotto is imported)

from sotto import config, plat, scene, widgets  # noqa: E402
from sotto.grid import PALETTE, Grid, set_palette, to_png  # noqa: E402

CFG = Path(os.environ["SOTTO_CONFIG"])


def write_config(text: str):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    CFG.write_text(text, encoding="utf-8")
    t = time.time() + 1                 # a new mtime even on coarse filesystem clocks
    os.utime(CFG, (t, t))


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;?<]*[A-Za-z]", "", s)


class Config(unittest.TestCase):
    def tearDown(self):
        CFG.unlink(missing_ok=True)
        set_palette(config.THEMES["fresco"])

    def test_defaults_without_a_file(self):
        CFG.unlink(missing_ok=True)
        s = config.load()
        self.assertEqual(s["palette"], config.THEMES["fresco"])
        self.assertEqual(s["layout"], {"left": ["agents"], "right": ["today"]})
        self.assertEqual(config.check(), [])

    def test_template_is_valid(self):
        path, wrote = config.init("Sam", force=True)
        self.assertTrue(wrote)
        self.assertEqual(config.check(), [])
        s = config.load()
        self.assertIn("focus", s["layout"]["right"])

    def test_check_finds_mistakes(self):
        write_config('theme = "neon"\ncolour = 1\n[colors]\nbg = "red"\n[layout]\nleft = ["nope"]\n'
                     '[[widget]]\nid = "w"\ntext = ["a"]\ncommand = "echo"\n')
        probs = " | ".join(config.check())
        for want in ("neon", "unknown key 'colour'", "should look like", "'nope'", "exactly one of"):
            self.assertIn(want, probs)

    def test_theme_and_colour_override(self):
        write_config('theme = "night"\n[colors]\naccent = "#00ff00"\n')
        pal = config.load()["palette"]
        self.assertEqual(pal["bg"], config.THEMES["night"]["bg"])
        self.assertEqual(pal["accent"], "#00ff00")

    def test_unplaced_widgets_go_under_today(self):
        write_config('[[widget]]\nid = "a"\ntext = ["x"]\n')
        self.assertEqual(config.load()["layout"]["right"], ["today", "a"])


class Widgets(unittest.TestCase):
    def test_text_file_command_and_json(self):
        self.assertEqual(widgets.run_once({"text": ["hi"], "lines": 8}), [("hi", "")])
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("one\n\ntwo\nthree\n")
        try:
            rows = widgets.run_once({"file": f.name, "lines": 2})
            self.assertEqual([r[0] for r in rows], ["one", "two"])
        finally:
            os.unlink(f.name)
        rows = widgets.run_once({"command": "echo hello", "lines": 8, "timeout": 10})
        self.assertEqual(rows[0][0].strip(), "hello")
        self.assertEqual(widgets.parse('{"lines": ["a", {"text": "b", "color": "alert"}]}', 8),
                         [("a", ""), ("b", "alert")])
        self.assertEqual(widgets.parse("\x1b[31mred\x1b[0m\n", 8), [("red", "")])

    def test_failing_command_is_one_alert_row(self):
        rows = widgets.run_once({"command": "exit 3", "lines": 8, "timeout": 10})
        self.assertEqual(rows[0][1], "alert")


class Scene(unittest.TestCase):
    def test_layout_and_widget_render(self):
        write_config('[layout]\nleft = ["mine"]\nright = []\n[[widget]]\nid = "mine"\ntitle = "MINE"\ntext = ["hello there"]\n')
        try:
            s = config.load()
            g, _ = scene.build({"user": "sam"}, 160, 48, settings=s, widget_rows=widgets.run_all(s["widgets"]))
            text = "\n".join("".join(g.ch[r * 160:(r + 1) * 160]) for r in range(48))
            self.assertIn("MINE", text)
            self.assertIn("hello there", text)
            self.assertNotIn("AGENTS", text)
        finally:
            CFG.unlink(missing_ok=True)

    def test_preview_png(self):
        from sotto.art import font_path

        g, _ = scene.build({"user": "sam", "today": {"configured": True, "items": [{"when": "9:00", "title": "x"}]}},
                           120, 40, settings=config.load())
        out = Path(tempfile.mkdtemp()) / "p.png"
        to_png(g, str(out), font_path())
        self.assertGreater(out.stat().st_size, 1000)


class Platform(unittest.TestCase):
    def test_stats_and_processes(self):
        st = plat.stats()
        self.assertIsInstance(st["ram"], int)
        self.assertGreaterEqual(st["disk"], 0)
        self.assertTrue(plat.alive(os.getpid()))
        self.assertFalse(plat.alive(2_000_000_000))
        self.assertTrue(plat.alive(os.getpid()))     # still alive: checking never kills (Windows)

    def test_assistant_local_answers(self):
        from sotto import assistant

        self.assertTrue(assistant.handle("what time is it")["reply"].startswith("It's"))
        self.assertIn("RAM", assistant.handle("how much ram")["reply"])
        self.assertEqual(assistant.handle("open youtube")["action"]["type"], "url")
        self.assertIsNotNone(assistant.handle("lock")["action"]["argv"])

    @unittest.skipIf(plat.OS == "windows", "no pty on Windows")
    def test_shell_in_a_pty(self):
        from sotto.term import Term

        t = Term(0, 0, cols=60, rows=10)
        try:
            t.send(b"echo sotto-$((40+2))\r")
            end, seen = time.time() + 8, ""
            while time.time() < end and "sotto-42" not in seen:
                t.read()
                seen = "\n".join(t.screen.display)
                time.sleep(0.05)
            self.assertIn("sotto-42", seen)
        finally:
            t.close()


class WebWall(unittest.TestCase):
    """A real wall drawing to the web transport, driven like a browser would."""

    @classmethod
    def setUpClass(cls):
        CFG.unlink(missing_ok=True)
        cls.h = harness.Harness(160, 48)
        cls.first = cls.h.drain(1.5)

    @classmethod
    def tearDownClass(cls):
        cls.h.stop()
        CFG.unlink(missing_ok=True)
        set_palette(config.THEMES["fresco"])

    def test_1_first_frame(self):
        self.assertGreater(len(self.first), 2000)
        self.assertIn("\x1b[?1003h", self.first)       # any-motion mouse reports on

    def test_2_mouse_trail(self):
        for i in range(25):
            self.h.move(10 + i, 44)
        out = strip_ansi(self.h.drain(0.5))
        self.assertTrue(set(out) & set("·•∙01"), "no trail dots drawn")

    def test_3_prompt(self):
        self.h.click(20, 40)
        self.h.click(20, 40)                           # double-click: no dock here, so the prompt is in the wall
        self.h.drain(0.3)
        self.assertTrue(self.h.focus and self.h.focus[-1] is True, "the prompt didn't take the keyboard")
        self.h.feed("what time is it\r")
        out = strip_ansi(self.h.drain(1.5))
        self.assertIn("It's", out.replace(" ", ""))   # the answer types itself out over several frames
        self.assertIs(self.h.focus[-1], False)        # keyboard handed back after Enter
        self.h.feed("\x1b")

    def test_4_live_theme(self):
        write_config('theme = "night"\n')
        out = self.h.drain(2.5)
        r, g, b = (int(config.THEMES["night"]["bg"][i:i + 2], 16) for i in (1, 3, 5))
        self.assertIn(f"48;2;{r};{g};{b}m", out)


class Server(unittest.TestCase):
    def test_token_and_host_checks(self):
        from sotto import web

        srv = web.start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"

            def status(path, data=None, headers=None):
                req = urllib.request.Request(base + path, data=data, headers=headers or {})
                try:
                    with urllib.request.urlopen(req, timeout=5) as r:
                        return r.status
                except urllib.error.HTTPError as e:
                    return e.code

            self.assertEqual(status("/"), 403)
            self.assertEqual(status(f"/?t={srv.token}"), 200)
            self.assertEqual(status("/static/xterm.js"), 200)
            self.assertEqual(status("/input", b"x"), 403)
            self.assertEqual(status("/input", b"x", {"X-Sotto-Token": srv.token}), 204)
            self.assertEqual(status("/input", b"x", {"X-Sotto-Token": srv.token, "Host": "evil.example"}), 403)
            self.assertEqual(status("/resize", json.dumps({"cols": 100, "rows": 30}).encode(),
                                    {"X-Sotto-Token": srv.token}), 204)
            self.assertEqual(srv.io.size(), (100, 30))
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
