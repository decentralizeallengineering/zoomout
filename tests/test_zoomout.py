import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import zoomout

CLI = os.path.join(HERE, "..", "zoomout.py")


def bash(cmd, out="", **resp):
    return {"tool_name": "Bash", "tool_input": {"command": cmd},
            "tool_response": dict({"stdout": out, "stderr": ""}, **resp)}


def edit(path):
    return {"tool_name": "Edit", "tool_input": {"file_path": path}, "tool_response": {}}


def feed(events, cfg=None):
    state, notes = zoomout.new_state(), []
    for ev in events:
        notes += zoomout.process(ev, state, cfg or zoomout.DEFAULTS)
    return notes


class ZoomoutTests(unittest.TestCase):
    def test_each_trigger_kind_fires(self):
        failing = bash("python3 -m pytest -q", "2 failed, 1 passed")
        cases = {
            "test_fail": [failing] * 3,
            "edit_churn": [edit("/work/a.py")] * 5,
            "retry": [bash("curl -s http://127.0.0.1:9/x")] * 3,
            "stall": [edit("/work/a.py")] + [bash(f"echo {i}") for i in range(30)],
        }
        for kind, events in cases.items():
            with self.subTest(kind=kind):
                notes = feed(events)
                self.assertEqual(len(notes), 1, notes)
                self.assertTrue(notes[0].startswith("consider /zoomout: "))
        self.assertIn("failed 3 times", feed(cases["test_fail"])[0])
        self.assertEqual(feed([failing] * 2 + [bash("python3 -m pytest -q", "3 passed")] + [failing] * 2), [])

    def test_failed_commands_arrive_as_post_tool_use_failure(self):
        # shape captured from Claude Code 2.1: a non-zero exit fires only PostToolUseFailure, with no tool_response
        fail = {"hook_event_name": "PostToolUseFailure", "session_id": "s2", "tool_name": "Bash",
                "tool_input": {"command": "python -m pytest -q", "description": "Run tests"},
                "error": "Exit code 1\n1 failed, 2 passed", "is_interrupt": False}
        timeout = dict(fail, error="Exit code 143\nCommand timed out after 2m")
        self.assertEqual(len(feed([fail, timeout, fail])), 1)
        self.assertEqual(feed([dict(fail, is_interrupt=True)] * 3), [])
        interrupted = bash("python -m pytest -q", "", interrupted=True)
        self.assertEqual(feed([fail, fail, interrupted]), [])  # an interrupt is ignored, not a failure
        self.assertEqual(feed([fail, fail, interrupted, fail]), feed([fail] * 3))  # and not a pass
        failed_edit = {"hook_event_name": "PostToolUseFailure", "tool_name": "Edit", "error": "String to replace not found",
                       "tool_input": {"file_path": "/work/new.py"}}
        self.assertEqual(len(feed([edit("/work/a.py")] + [failed_edit] * 30)), 1)  # activity, not progress: stall fires
        with tempfile.TemporaryDirectory() as state_dir:
            env = dict(os.environ, ZOOMOUT_STATE_DIR=state_dir)
            env.pop("ZOOMOUT_OFF", None)
            procs = [subprocess.run([sys.executable, CLI, "hook"], input=json.dumps(fail), env=env, capture_output=True,
                                    text=True, timeout=20, check=False) for _ in range(3)]
        self.assertEqual([(p.returncode, p.stdout) for p in procs[:2]], [(0, ""), (0, "")])
        self.assertEqual(procs[2].returncode, 0)
        out = json.loads(procs[2].stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUseFailure")  # Claude Code rejects a mismatched name
        self.assertIn("failed 3 times", out["additionalContext"])

    def test_fires_once_per_kind(self):
        notes = feed([edit("/work/a.py")] * 12)
        self.assertEqual(len(notes), 1)
        both = feed([edit("/work/a.py")] * 5 + [bash("make deploy")] * 6)
        self.assertEqual(len(both), 2)  # edit_churn once, retry once, nothing repeated

    def test_prompt_assembly(self):
        text = zoomout.build_prompt("ship the importer", ["regex parser: breaks on nested quotes", "retry loop"],
                                    "no new dependencies", ["parsing"])
        for needle in ("ship the importer", "- regex parser: breaks on nested quotes", "- retry loop",
                       "no new dependencies", "parsing", "5 lines", "archetypal roles",
                       "never name or imitate real people", "CONTINUE, PIVOT to", "SIMPLIFY to",
                       "FIRST STEP:", "standard proven approach", "known pitfall", "simplest thing"):
            self.assertIn(needle, text)

    def test_dry_run_prints_command_and_runs_nothing(self):
        base = [sys.executable, CLI, "run", "--goal", "G", "--tried", "T", "--dry-run",
                "--claude-bin", "/nonexistent/claude"]
        plain = subprocess.run(base, capture_output=True, text=True, timeout=20, check=False)
        fresh = subprocess.run([*base, "--fresh", "--model", "m1"], capture_output=True, text=True,
                               timeout=20, check=False)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertTrue(plain.stdout.startswith("/nonexistent/claude -p "))
        self.assertTrue(fresh.stdout.startswith("cd <new empty temp dir> && CLAUDE_CONFIG_DIR="))
        self.assertIn("--model m1", fresh.stdout)

    def test_hook_cli_suggests_once_and_never_blocks(self):
        with tempfile.TemporaryDirectory() as state_dir:
            env = dict(os.environ, ZOOMOUT_STATE_DIR=state_dir)
            env.pop("ZOOMOUT_OFF", None)
            outs = []
            for _ in range(7):
                ev = dict(edit("/work/a.py"), session_id="s1")
                proc = subprocess.run([sys.executable, CLI, "hook"], input=json.dumps(ev), env=env,
                                      capture_output=True, text=True, timeout=20, check=False)
                self.assertEqual(proc.returncode, 0)
                outs.append(proc.stdout)
        fired = [o for o in outs if o]
        self.assertEqual(len(fired), 1)
        data = json.loads(fired[0])
        self.assertNotIn("decision", data)
        self.assertIn("consider /zoomout:", data["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
