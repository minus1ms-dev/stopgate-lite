"""End-to-end tests for StopGate Lite using a fake `claude` binary (no API calls)."""
import json, os, pathlib, shutil, subprocess, sys, tempfile, unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
gate = "StopChecker" in prompt
verdict = os.environ["FAKE_VERDICT"]
body = {"verdict": "issue" if verdict != "ok" else "ok",
        "continue": gate and verdict == "continue",
        "feedback": "" if verdict == "ok" else "do more work"}
print(json.dumps({"result": json.dumps(body), "total_cost_usd": 0}))
"""


class StopGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        fake = self.tmp / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{self.tmp}:{os.environ['PATH']}"}
        self.env.pop("STOPGATE_CHILD", None)
        subprocess.run(["bash", str(ROOT / "install.sh")], env=self.env, check=True, capture_output=True)
        self.hook = self.home / ".claude/hooks/stopgate/stopgate.py"
        self.transcript = self.tmp / "t.jsonl"
        self.transcript.write_text("\n".join(json.dumps(e) for e in [
            {"type": "user", "message": {"content": "fix the bug"}, "timestamp": "2026-01-01T00:00:00Z"},
            {"type": "assistant", "message": {"id": "m1", "model": "x", "content": [{"type": "text", "text": "done"}],
                                              "usage": {"output_tokens": 5}}, "timestamp": "2026-01-01T00:00:05Z"},
        ]) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def stop(self, verdict, active=False, **env):
        inp = {"session_id": "s1", "transcript_path": str(self.transcript), "stop_hook_active": active, "cwd": "/"}
        out = subprocess.run([sys.executable, str(self.hook)], input=json.dumps(inp), text=True, capture_output=True,
                             env={**self.env, "FAKE_VERDICT": verdict, **env}, check=True)
        return json.loads(out.stdout) if out.stdout.strip() else None

    def test_install_registers_hook_once(self):
        subprocess.run(["bash", str(ROOT / "install.sh")], env=self.env, check=True, capture_output=True)
        cfg = json.loads((self.home / ".claude/settings.json").read_text())
        cmds = [h["command"] for g in cfg["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(cmds, [str(self.hook)])

    def test_all_ok_allows_stop(self):
        self.assertIsNone(self.stop("ok"))

    def test_issue_sends_feedback_once(self):
        out = self.stop("issue")
        self.assertEqual(out["decision"], "block")
        self.assertIn("acceptable", out["reason"])
        self.assertIsNone(self.stop("issue", active=True))

    def test_gate_forces_continue(self):
        out = self.stop("continue")
        self.assertIn("rejected", out["reason"])
        self.assertIn("do more work", out["reason"])

    def test_forced_continue_limit(self):
        self.assertIsNotNone(self.stop("continue", STOPGATE_MAX_FORCED="2"))
        self.assertIsNotNone(self.stop("continue", active=True, STOPGATE_MAX_FORCED="2"))
        out = self.stop("continue", active=True, STOPGATE_MAX_FORCED="2")
        self.assertNotIn("rejected", (out or {}).get("reason", ""))

    def test_disable_env(self):
        self.assertIsNone(self.stop("continue", STOPGATE_DISABLE="1"))

    def test_uninstall(self):
        subprocess.run(["bash", str(ROOT / "uninstall.sh")], env=self.env, check=True, capture_output=True, cwd=self.tmp)
        cfg = json.loads((self.home / ".claude/settings.json").read_text())
        self.assertEqual(cfg["hooks"]["Stop"], [])
        self.assertFalse(self.hook.exists())


if __name__ == "__main__":
    unittest.main()
