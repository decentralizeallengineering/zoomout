import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import zoomout  # noqa: E402

CLI = [sys.executable, str(ROOT / "zoomout.py")]
OPS = {name: getattr(zoomout, name) for name in zoomout.SCHEMAS}


def cli(*args, **kw):
    return subprocess.run([*CLI, *args], capture_output=True, text=True, encoding="utf-8", timeout=30,
                          check=False, **kw)


def hook_cli(event, state_dir, script=None):
    env = dict(os.environ, ZOOMOUT_STATE_DIR=str(state_dir))
    env.pop("ZOOMOUT_OFF", None)
    return subprocess.run([sys.executable, str(script or ROOT / "zoomout.py"), "hook"], input=json.dumps(event),
                          env=env, capture_output=True, text=True, timeout=30, check=False)


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


# ------------------------------------------------------------------ operations
def test_schemas_are_small_valid_and_match_the_functions():
    assert set(OPS) == {"install", "init", "prompt", "run", "hook"}
    for name, schema in zoomout.SCHEMAS.items():
        assert len(json.dumps(schema, separators=(",", ":"))) <= 600, name
        jsonschema.Draft202012Validator.check_schema(schema)
        assert set(schema["properties"]) == set(inspect.signature(OPS[name]).parameters), name
    # user errors come back as reports, never as exceptions
    for rep in (zoomout.prompt(""), zoomout.run(goal=None), zoomout.install(project="/no/such/dir"),
                zoomout.hook(event="not an event"), zoomout.prompt("g", bogus=1)):
        json.dumps(rep)
        assert set(rep) == {"ok", "changed", "warnings", "details"} and rep["ok"] is False and rep["warnings"]


def test_install_into_a_project_then_init_prints_a_working_hook(tmp_path):
    proc = cli("install", "--project", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    claude = tmp_path / ".claude"
    command, skill_dir = claude / "commands" / "zoomout.md", claude / "skills" / "zoomout"
    assert command.read_text(encoding="utf-8") == zoomout.COMMAND_MD
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == zoomout.SKILL_MD
    assert (skill_dir / "zoomout.py").read_bytes() == (ROOT / "zoomout.py").read_bytes()
    again = zoomout.install(project=str(tmp_path))
    assert again["ok"] and again["changed"] == []
    command.write_text("edited by Ann Example\n", encoding="utf-8")
    blocked = zoomout.install(project=str(tmp_path))
    assert not blocked["ok"] and "differs" in blocked["warnings"][0]
    assert command.read_text(encoding="utf-8") == "edited by Ann Example\n"
    assert zoomout.install(project=str(tmp_path), force=True)["changed"] == [str(command)]

    printed = cli("init", "--print", "--project", str(tmp_path))
    assert printed.returncode == 0 and "never edits settings" in printed.stderr
    hooks = json.loads(printed.stdout)["hooks"]
    assert set(hooks) == {"PostToolUse", "PostToolUseFailure"}
    assert hooks["PostToolUse"][0]["matcher"] == "*"
    assert "$CLAUDE_PROJECT_DIR/.claude/skills/zoomout/zoomout.py" in hooks["PostToolUse"][0]["hooks"][0]["command"]
    assert not (claude / "settings.json").exists()
    assert cli("init").returncode != 0  # --print is required: init only ever prints
    # the installed copy works as the hook
    ran = hook_cli(dict(edit("/work/a.py"), session_id="s0"), tmp_path / "state", skill_dir / "zoomout.py")
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")


def test_user_install_goes_to_claude_config_dir(tmp_path):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"))
    assert cli("install", env=env).returncode == 0
    assert (tmp_path / "cfg" / "commands" / "zoomout.md").is_file()
    snippet = json.loads(cli("init", "--print", env=env).stdout)
    command = snippet["hooks"]["PostToolUseFailure"][0]["hooks"][0]["command"]
    assert (tmp_path / "cfg" / "skills" / "zoomout" / "zoomout.py").as_posix() in command


def test_shipped_command_and_skill_are_what_install_writes():
    for rel, text in ((".claude/commands/zoomout.md", zoomout.COMMAND_MD), ("SKILL.md", zoomout.SKILL_MD)):
        assert (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n") == text, rel
    for text in (zoomout.COMMAND_MD, zoomout.SKILL_MD):
        assert 'argument-hint: "[what you are stuck on]"' in text and "$ARGUMENTS" in text  # quoted, or YAML eats [ ]
        assert zoomout.PROCEDURE in text


def test_prompt_carries_the_context_and_the_procedure():
    text = zoomout.prompt("ship the importer", ["regex parser: breaks on nested quotes", "retry loop"],
                          "no new dependencies", ["parsing"])["details"]["prompt"]
    for needle in ("GOAL: ship the importer", "- regex parser: breaks on nested quotes", "no new dependencies",
                   "parsing", "KEY FACT:", "never a real or named person", "VERDICT:", "FIRST STEP:"):
        assert needle in text


FAKE_CLAUDE = r'''import json, os, sys
prompt = sys.stdin.read()
print(json.dumps({"cwd": os.getcwd(), "config": os.environ.get("CLAUDE_CONFIG_DIR"), "argv": sys.argv[1:],
                  "empty": os.listdir(os.environ["CLAUDE_CONFIG_DIR"]) == [] and os.listdir(".") == [],
                  "goal": "GOAL: G" in prompt}))
print("**VERDICT:** PIVOT to the gateway audience config")
print("FIRST STEP: curl -sI the refresh endpoint and read the aud claim")
'''


def fake_claude(tmp_path):
    (tmp_path / "fake.py").write_text(FAKE_CLAUDE, encoding="utf-8")
    if os.name == "nt":
        exe = tmp_path / "claude.cmd"
        exe.write_text(f'@"{sys.executable}" "{tmp_path / "fake.py"}" %*\n', encoding="utf-8")
    else:
        exe = tmp_path / "claude"
        exe.write_text(f'#!{sys.executable}\nexec(open({str(tmp_path / "fake.py")!r}).read())\n', encoding="utf-8")
        exe.chmod(0o755)
    return str(exe)


def test_run_uses_an_empty_temp_dir_and_config_dir_then_parses_the_verdict(tmp_path):
    rep = zoomout.run("G", ["T"], model="m1", claude_bin=fake_claude(tmp_path))
    assert rep["ok"], rep
    seen = json.loads(rep["details"]["output"].splitlines()[0])
    assert seen["empty"] and seen["goal"] and seen["argv"] == ["-p", "--model", "m1"]
    assert not os.path.exists(seen["cwd"]) and not os.path.exists(seen["config"])  # cleaned up
    assert rep["details"]["verdict_class"] == "PIVOT"
    assert rep["details"]["verdict"] == "PIVOT to the gateway audience config"
    assert rep["details"]["first_step"].startswith("curl -sI")
    dry = cli("run", "--fresh", "--dry-run", "--goal", "G", "--tried", "T", "--claude-bin", "/nonexistent/claude")
    assert dry.returncode == 0 and dry.stdout.startswith("cd <new empty temp dir> && CLAUDE_CONFIG_DIR=")
    assert cli("run", "--goal", "G").returncode != 0  # --fresh is required: it is the only mode
    assert zoomout.run("G", model="m1; rm -rf /")["ok"] is False
    missing = zoomout.run("G", claude_bin=str(tmp_path / "no-such-claude"))
    assert not missing["ok"] and "not found" in missing["warnings"][0]


# ------------------------------------------------------------------ hook
def test_each_trigger_kind_fires():
    failing = bash("python3 -m pytest -q", "2 failed, 1 passed")
    cases = {
        "test_fail": [failing] * 3,
        "edit_churn": [edit("/work/a.py")] * 5,
        "retry": [bash("curl -s http://127.0.0.1:9/x")] * 3,
        "stall": [edit("/work/a.py")] + [bash(f"echo {i}") for i in range(30)],
    }
    for kind, events in cases.items():
        notes = feed(events)
        assert len(notes) == 1 and notes[0].startswith("consider /zoomout: "), (kind, notes)
    assert "failed 3 times" in feed(cases["test_fail"])[0]
    assert feed([failing] * 2 + [bash("python3 -m pytest -q", "3 passed")] + [failing] * 2) == []


def test_failed_commands_arrive_as_post_tool_use_failure(tmp_path):
    # shape captured from Claude Code 2.1: a non-zero exit fires only PostToolUseFailure, with no tool_response
    fail = {"hook_event_name": "PostToolUseFailure", "session_id": "s2", "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest -q", "description": "Run tests"},
            "error": "Exit code 1\n1 failed, 2 passed", "is_interrupt": False}
    timeout = dict(fail, error="Exit code 143\nCommand timed out after 2m")
    assert len(feed([fail, timeout, fail])) == 1
    assert feed([dict(fail, is_interrupt=True)] * 3) == []
    interrupted = bash("python -m pytest -q", "", interrupted=True)
    assert feed([fail, fail, interrupted]) == []  # an interrupt is ignored, not a failure
    assert feed([fail, fail, interrupted, fail]) == feed([fail] * 3)  # and not a pass
    failed_edit = {"hook_event_name": "PostToolUseFailure", "tool_name": "Edit", "error": "String to replace not found",
                   "tool_input": {"file_path": "/work/new.py"}}
    assert len(feed([edit("/work/a.py")] + [failed_edit] * 30)) == 1  # activity, not progress: stall fires
    procs = [hook_cli(fail, tmp_path) for _ in range(3)]
    assert [(p.returncode, p.stdout) for p in procs[:2]] == [(0, ""), (0, "")]
    assert procs[2].returncode == 0
    out = json.loads(procs[2].stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolUseFailure"  # Claude Code rejects a mismatched name
    assert "failed 3 times" in out["additionalContext"]


def test_fires_once_per_kind():
    assert len(feed([edit("/work/a.py")] * 12)) == 1
    both = feed([edit("/work/a.py")] * 5 + [bash("make deploy")] * 6)
    assert len(both) == 2  # edit_churn once, retry once, nothing repeated


def test_hook_cli_suggests_once_and_never_blocks(tmp_path):
    outs = []
    for _ in range(7):
        proc = hook_cli(dict(edit("/work/a.py"), session_id="s1"), tmp_path)
        assert proc.returncode == 0
        outs.append(proc.stdout)
    fired = [o for o in outs if o]
    assert len(fired) == 1
    data = json.loads(fired[0])
    assert "decision" not in data
    assert "consider /zoomout:" in data["hookSpecificOutput"]["additionalContext"]
    for junk in ("", "not json", "[1, 2]", "null"):
        proc = subprocess.run([*CLI, "hook"], input=junk, capture_output=True, text=True, timeout=30, check=False,
                              env=dict(os.environ, ZOOMOUT_STATE_DIR=str(tmp_path)))
        assert (proc.returncode, proc.stdout) == (0, ""), junk
