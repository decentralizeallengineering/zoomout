#!/usr/bin/env python3
"""zoomout: make a stuck coding agent step back and end with CONTINUE, PIVOT or SIMPLIFY.

Each operation is a plain function that returns {"ok", "changed", "warnings", "details"} and never raises;
SCHEMAS[name] is the JSON Schema of its arguments, so an LLM can call it as a tool.

  install  copy the /zoomout command, the skill and this script into ~/.claude or a project
  init     return the hook settings snippet (the CLI prints it; zoomout never edits settings)
  prompt   build the step-back prompt
  run      answer the prompt with headless `claude -p` in an empty temp dir and config dir
  hook     PostToolUse hook: suggest /zoomout when a trigger fires; never blocks

The only subprocess is _claude_fresh(), used by `run`. zoomout makes no network calls and sends no telemetry.
Config: JSON, first found of $ZOOMOUT_CONFIG, ./.claude/zoomout.json, zoomout.json next to this script.
Hook state: $ZOOMOUT_STATE_DIR (default ~/.cache/zoomout), one file per session. ZOOMOUT_OFF=1 disables the hook.
"""
import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROCEDURE = """\
Stop. Do not run tools or edit files until the verdict is written. Keep the whole answer under 35 lines.

1. GOAL AND CONSTRAINTS: the outcome actually needed (not the current fix) and the hard constraints, at most 5 lines.
2. TRIED AND WHY IT FAILED: one line per attempt with the most likely reason it failed. Then one line \
"KEY FACT: <quote>" quoting the single observed fact from the session (an error, log line, header, number or \
search hit) that best explains the failures, or "KEY FACT: none".
3. FIELDS: the field or fields the problem really belongs to. The symptom shows where the problem appears, \
not always where it lives.
4. EXPERT VIEW: for each field, an archetypal practitioner (for example "a senior SRE at a large-scale operator" \
or "a compiler engineer"; never a real or named person) and three short items: the standard proven approach, \
the known pitfall, and the simplest thing that could work.
5. COMPARISON: where the current approach matches those views, where it diverges, and which effort is sunk.
6. VERDICT: choose exactly one.
   CONTINUE: keep the current approach. It is the standard one and the evidence shows it converging.
   PIVOT to <X>: replace the current approach. It works in the wrong place, on the wrong cause, or with a method \
that cannot converge. X is where or how to work instead.
   SIMPLIFY to <Y>: keep the target but shrink the solution, which is bigger than the problem. Y deletes or \
skips something, cuts scope, or uses an existing standard tool instead of hand-built code.
   Do not abandon converging work because the session feels long. X or Y must name the specific thing (file, \
service, query, setting, library or test), never "a different approach".

End with exactly these two lines and nothing after them:
VERDICT: <CONTINUE, or PIVOT to X, or SIMPLIFY to Y>
FIRST STEP: <one concrete action that names a specific file, command or setting from this session, and the \
result that would confirm the verdict>
"""

COMMAND_MD = f"""\
---
description: Step back from a stuck approach and get a verdict of CONTINUE, PIVOT or SIMPLIFY
argument-hint: "[what you are stuck on]"
---

Zoom out on the current task. Extra context from the user: $ARGUMENTS

{PROCEDURE}"""

SKILL_MD = f"""\
---
name: zoomout
description: Step back when stuck or about to sink more effort into one approach, and end with a verdict of \
CONTINUE, PIVOT or SIMPLIFY plus a first step. Use when the same test keeps failing, one file is edited again and \
again, a command is retried, progress has stalled, or a hook says "consider /zoomout".
argument-hint: "[what you are stuck on]"
---

Zoom out on the current task. Extra context from the user: $ARGUMENTS

{PROCEDURE}
For a second opinion not anchored on this session, the user can run the bundled script:
`python3 "${{CLAUDE_SKILL_DIR}}/zoomout.py" run --fresh --goal "<goal>" --tried "<attempt and outcome>"`
"""

FRESH_INTRO = """\
An engineering agent is stuck or about to sink more effort into one approach, and you are asked to zoom out. \
Answer from your own knowledge: do not use tools, read files or write code.

GOAL: {goal}
HARD CONSTRAINTS: {constraints}
WHAT WAS TRIED, IN ORDER:
{tried}
{fields}
"""

DEFAULTS = {
    "test_fail_limit": 3,
    "edit_limit": 5,
    "retry_limit": 3,
    "stall_turns": 25,
    "test_command_patterns": [
        r"\bpytest\b", r"python[0-9.]*\s+-m\s+(pytest|unittest)\b",
        r"\b(npm|pnpm|yarn)\s+(run\s+)?test\b", r"\bcargo\s+test\b", r"\bgo\s+test\b",
        r"\bmake\s+(test|check)\b", r"\b(ruff|mypy|tsc|eslint)\b",
    ],
    "commit_patterns": [r"\bgit\s+commit\b"],
    "edit_tools": ["Edit", "Write", "MultiEdit", "NotebookEdit"],
}

FAIL_RE = re.compile(  # failing output from a command that still exited 0, e.g. `pytest | tail`
    r"(\b[1-9]\d*\s+(failed|errors?)\b|\bFAILED\b|Traceback \(most recent|\bAssertionError\b|"
    r"npm ERR!|error TS\d+|^FAIL\b)", re.MULTILINE)
VERDICT_RE = re.compile(r"VERDICT\W{0,4}\s*((CONTINUE|PIVOT|SIMPLIFY)\b[^\n]*)", re.IGNORECASE)
FIRST_STEP_RE = re.compile(r"FIRST STEP\W{0,4}\s*([^\n]+)", re.IGNORECASE)

S = {"type": "string"}
LIST = {"type": "array", "items": S}
ASK = {"goal": dict(S, description="outcome needed"),
       "tried": dict(LIST, description="attempts and outcomes, in order"),
       "constraints": dict(S, description="hard constraints"),
       "fields": dict(LIST, description="suggested fields")}
PROJECT = dict(S, description="project directory; omit for the user-level ~/.claude")
SCHEMAS = {
    "install": {"type": "object", "additionalProperties": False, "properties": {
        "project": PROJECT, "force": {"type": "boolean", "description": "replace differing files"}}},
    "init": {"type": "object", "additionalProperties": False, "properties": {"project": PROJECT}},
    "prompt": {"type": "object", "additionalProperties": False, "required": ["goal"], "properties": ASK},
    "run": {"type": "object", "additionalProperties": False, "required": ["goal"], "properties": dict(
        ASK, model=dict(S, description="claude --model value"), claude_bin=dict(S, description="claude executable"),
        timeout={"type": "integer", "minimum": 1}, dry_run={"type": "boolean"})},
    "hook": {"type": "object", "additionalProperties": False, "required": ["event"], "properties": {
        "event": {"type": "object", "description": "hook input JSON from Claude Code"},
        "state_dir": dict(S, description="where per-session state is kept")}},
}


def _report(ok=True, changed=(), warnings=(), **details):
    return {"ok": ok, "changed": [str(c) for c in changed], "warnings": list(warnings), "details": details}


def _operation(fn):
    @functools.wraps(fn)
    def safe(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001  a user error becomes a report, never an exception
            return _report(False, warnings=[f"{fn.__name__}: {type(exc).__name__}: {exc}"])
    return safe


def load_config():
    cfg = dict(DEFAULTS)
    for path in (os.environ.get("ZOOMOUT_CONFIG"), os.path.join(".claude", "zoomout.json"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "zoomout.json")):
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    cfg.update(json.load(fh))
            except (OSError, ValueError):
                continue
            break
    return cfg


# ------------------------------------------------------------------ install / init
def _claude_dir(project):
    if project:
        return Path(project).expanduser() / ".claude"
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


@_operation
def install(project=None, force=False):
    """Copy the /zoomout command, the skill and this script into ~/.claude or <project>/.claude."""
    if project and not Path(project).expanduser().is_dir():
        return _report(False, warnings=[f"project directory not found: {project}"])
    root = _claude_dir(project)
    plan = {root / "commands" / "zoomout.md": COMMAND_MD.encode(),
            root / "skills" / "zoomout" / "SKILL.md": SKILL_MD.encode(),
            root / "skills" / "zoomout" / "zoomout.py": Path(__file__).read_bytes()}
    differ = [p for p, data in plan.items() if p.is_file() and p.read_bytes() != data]
    if differ and not force:
        return _report(False, warnings=[f"{p} exists and differs; use force to replace it" for p in differ])
    changed = []
    for path, data in plan.items():
        if not (path.is_file() and path.read_bytes() == data):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            changed.append(path)
    return _report(changed=changed, root=str(root))


@_operation
def init(project=None):
    """Return the hook settings snippet for ~/.claude or <project>/.claude. Never edits settings."""
    root = _claude_dir(project)
    script = root / "skills" / "zoomout" / "zoomout.py"
    if project:
        command = 'python3 "$CLAUDE_PROJECT_DIR/.claude/skills/zoomout/zoomout.py" hook'
    else:
        command = f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}" hook'
    entry = [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]
    warnings = [] if script.is_file() else [f"{script} not found: install first"]
    return _report(warnings=warnings, settings_file=str(root / "settings.json"),
                   snippet={"hooks": {"PostToolUse": entry, "PostToolUseFailure": entry}})


# ------------------------------------------------------------------ prompt / run
@_operation
def prompt(goal, tried=None, constraints=None, fields=None):
    """Build the step-back prompt for a fresh model."""
    if not isinstance(goal, str) or not goal.strip():
        return _report(False, warnings=["goal is required"])
    tried_text = "\n".join("- " + str(t) for t in tried) if tried else "- (nothing listed)"
    fields_line = f"FIELDS SUGGESTED BY THE CALLER (confirm or correct): {', '.join(fields)}\n" if fields else ""
    text = FRESH_INTRO.format(goal=goal.strip(), constraints=constraints or "(none stated; infer and flag as inferred)",
                              tried=tried_text, fields=fields_line) + "\n" + PROCEDURE
    return _report(prompt=text)


def _parse_verdict(text):
    """(class, verdict line, first step) from the last VERDICT and FIRST STEP lines of an answer."""
    verdicts, steps = VERDICT_RE.findall(text or ""), FIRST_STEP_RE.findall(text or "")
    clean = lambda s: re.sub(r"\*\*|__|`", "", s).strip(" *_:")  # noqa: E731
    if not verdicts:
        return None, None, clean(steps[-1]) if steps else None
    return verdicts[-1][1].upper(), clean(verdicts[-1][0]), clean(steps[-1]) if steps else None


def _claude_fresh(cmd, text, timeout):
    """The only subprocess in zoomout: `claude -p` with the prompt on stdin, run in a new empty working directory
    with a new empty CLAUDE_CONFIG_DIR, so the answer is not anchored on the stuck session's settings, memory or
    CLAUDE.md files. Both directories are deleted afterwards. Returns (returncode, stdout, stderr)."""
    exe = shutil.which(cmd[0])
    if not exe:
        return 127, "", f"{cmd[0]} not found on PATH"
    with tempfile.TemporaryDirectory(prefix="zoomout-", ignore_cleanup_errors=True) as tmp:
        work, config = os.path.join(tmp, "work"), os.path.join(tmp, "config")
        os.mkdir(work)
        os.mkdir(config)
        try:
            proc = subprocess.run([exe, *cmd[1:]], input=text, cwd=work, env=dict(os.environ, CLAUDE_CONFIG_DIR=config),
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 2, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


@_operation
def run(goal, tried=None, constraints=None, fields=None, model=None, claude_bin=None, timeout=300, dry_run=False):
    """Answer the step-back prompt with headless claude in an empty temp dir and config dir."""
    built = prompt(goal, tried, constraints, fields)
    if not built["ok"]:
        return built
    if model and not re.fullmatch(r"[\w.:\[\]-]+", model):
        return _report(False, warnings=["model must be a plain model name"])
    cmd = [claude_bin or os.environ.get("ZOOMOUT_CLAUDE") or "claude", "-p"] + (["--model", model] if model else [])
    if dry_run:
        return _report(command="cd <new empty temp dir> && CLAUDE_CONFIG_DIR=<another new empty temp dir> "
                       + " ".join(cmd) + " < <prompt>", prompt=built["details"]["prompt"])
    code, out, err = _claude_fresh(cmd, built["details"]["prompt"], timeout)
    verdict_class, verdict, first_step = _parse_verdict(out)
    warnings = [f"claude exited {code}: {err.strip()[-300:]}"] if code else [] if verdict else ["no VERDICT line"]
    return _report(code == 0, warnings=warnings, verdict_class=verdict_class, verdict=verdict, first_step=first_step,
                   output=out, returncode=code)


# ------------------------------------------------------------------ hook
def _matches(patterns, text):
    return any(re.search(p, text) for p in patterns)


def _failed(event):
    """Claude Code sends a non-zero Bash exit or a timeout as PostToolUseFailure (error="Exit code N\\n..."),
    and a zero exit as PostToolUse with tool_response.stdout."""
    if event.get("hook_event_name") == "PostToolUseFailure":
        return True
    resp = event.get("tool_response")
    text = "\n".join(str(resp.get(k) or "") for k in ("stdout", "stderr")) if isinstance(resp, dict) else str(resp or "")
    return bool(FAIL_RE.search(text))


def new_state():
    return {"since_progress": 0, "test_fails": {}, "edits": {}, "last_cmd": None, "streak": 0, "fired": []}


def process(event, state, cfg):
    """Update state for one PostToolUse or PostToolUseFailure event; return a list of new suggestion strings."""
    resp = event.get("tool_response")
    if event.get("is_interrupt") or (isinstance(resp, dict) and resp.get("interrupted")):
        return []  # the user stopped the tool: they are watching, not stuck
    tool = event.get("tool_name") or ""
    tin = event.get("tool_input") or {}
    state["since_progress"] += 1
    triggers = []
    progress = False
    if tool in cfg["edit_tools"] and event.get("hook_event_name") == "PostToolUseFailure":
        pass  # a failed edit changed nothing: activity, not an edit or progress
    elif tool in cfg["edit_tools"]:
        path = tin.get("file_path") or tin.get("notebook_path") or "?"
        n = state["edits"].get(path, 0) + 1
        state["edits"][path] = n
        progress = n == 1
        if n >= cfg["edit_limit"]:
            triggers.append(("edit_churn", f"the same file was edited {n} times this session ({path})"))
    elif tool == "Bash":
        cmd = " ".join((tin.get("command") or "").split())
        failed = _failed(event)
        if _matches(cfg["test_command_patterns"], cmd):
            state["last_cmd"], state["streak"] = None, 0
            if failed:
                n = state["test_fails"].get(cmd, 0) + 1
                state["test_fails"][cmd] = n
                if n >= cfg["test_fail_limit"]:
                    triggers.append(("test_fail", f"the same test failed {n} times ({cmd[:80]})"))
            else:
                state["test_fails"].pop(cmd, None)
                progress = True
        else:
            state["streak"] = state["streak"] + 1 if cmd == state["last_cmd"] else 1
            state["last_cmd"] = cmd
            if state["streak"] >= cfg["retry_limit"]:
                triggers.append(("retry", f"a command was retried {state['streak']} times ({cmd[:80]})"))
            if _matches(cfg["commit_patterns"], cmd) and not failed:
                progress = True
    if progress:
        state["since_progress"] = 0
    elif state["since_progress"] > cfg["stall_turns"]:
        triggers.append(("stall", f"{state['since_progress']} tool calls since the last progress signal"))
    out = []
    for kind, reason in triggers:
        if kind not in state["fired"]:
            state["fired"].append(kind)
            out.append("consider /zoomout: " + reason)
    return out


@_operation
def hook(event, state_dir=None):
    """Update this session's trigger state for one hook event and return any /zoomout suggestion."""
    if os.environ.get("ZOOMOUT_OFF"):
        return _report(suggestions=[], output=None)
    root = Path(state_dir or os.environ.get("ZOOMOUT_STATE_DIR") or Path.home() / ".cache" / "zoomout")
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(event.get("session_id") or "default")) or "default"
    path = root / (sid + ".json")
    state = new_state()
    try:
        state.update(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    notes = process(event, state, load_config())
    root.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)
    output = {"hookSpecificOutput": {"hookEventName": event.get("hook_event_name") or "PostToolUse",
                                     "additionalContext": "\n".join(notes)}} if notes else None
    return _report(changed=[path], suggestions=notes, output=output)


# ------------------------------------------------------------------ cli
def _cli_install(args):
    rep = install(args.project, args.force)
    for path in rep["changed"]:
        print("wrote", path)
    if rep["ok"] and not rep["changed"]:
        print("already installed in", rep["details"]["root"])
    if rep["ok"]:
        print("Type /zoomout in Claude Code. Optional hook: zoomout init --print"
              + (f" --project {args.project}" if args.project else ""))
    for w in rep["warnings"]:
        print("zoomout:", w, file=sys.stderr)
    return 0 if rep["ok"] else 1


def _cli_init(args):
    rep = init(args.project)
    if rep["ok"]:
        print(json.dumps(rep["details"]["snippet"], indent=2))
        print(f"Merge this into {rep['details']['settings_file']}. zoomout never edits settings.", file=sys.stderr)
    for w in rep["warnings"]:
        print("zoomout:", w, file=sys.stderr)
    return 0 if rep["ok"] else 1


def _cli_prompt(args):
    rep = prompt(args.goal, args.tried, args.constraints, args.field)
    sys.stdout.write(rep["details"].get("prompt", ""))
    for w in rep["warnings"]:
        print("zoomout:", w, file=sys.stderr)
    return 0 if rep["ok"] else 1


def _cli_run(args):
    rep = run(args.goal, args.tried, args.constraints, args.field, args.model, args.claude_bin, args.timeout,
              args.dry_run)
    d = rep["details"]
    sys.stdout.write(d["command"] + "\n" if args.dry_run and rep["ok"] else d.get("output", ""))
    for w in rep["warnings"]:
        print("zoomout:", w, file=sys.stderr)
    return 0 if rep["ok"] else d.get("returncode") or 1


def _cli_hook(_args):
    try:
        rep = hook(json.loads(sys.stdin.buffer.read().decode("utf-8", "replace")))
        if rep["details"].get("output"):
            sys.stdout.write(json.dumps(rep["details"]["output"]))
    except Exception:  # noqa: BLE001  suggestions only: never disturb the session
        pass
    return 0


def make_parser():
    ap = argparse.ArgumentParser(prog="zoomout", description="Step back and ask what top experts would do.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install", help="copy the /zoomout command, the skill and this script into ~/.claude")
    p.add_argument("--project", metavar="DIR", help="install into DIR/.claude instead")
    p.add_argument("--force", action="store_true", help="replace files that differ")
    p.set_defaults(fn=_cli_install)
    p = sub.add_parser("init", help="print the hook settings snippet (never edits settings)")
    p.add_argument("--print", action="store_true", required=True, help="print the snippet")
    p.add_argument("--project", metavar="DIR", help="snippet for DIR/.claude/settings.json")
    p.set_defaults(fn=_cli_init)

    def ask(p):
        p.add_argument("--goal", required=True, help="the outcome actually needed, one or two sentences")
        p.add_argument("--tried", action="append", default=[], help="an attempt and its outcome (repeatable)")
        p.add_argument("--constraints", help="hard constraints, if any")
        p.add_argument("--field", action="append", help="suggested field of expertise (repeatable)")

    p = sub.add_parser("prompt", help="print the step-back prompt")
    ask(p)
    p.set_defaults(fn=_cli_prompt)
    p = sub.add_parser("run", help="answer the step-back with headless claude -p in a clean config dir")
    ask(p)
    p.add_argument("--fresh", action="store_true", required=True,
                   help="required: run in a new empty working dir with a new empty CLAUDE_CONFIG_DIR")
    p.add_argument("--dry-run", action="store_true", help="print the command instead of running it")
    p.add_argument("--model", help="model name passed to claude --model")
    p.add_argument("--claude-bin", help="claude executable (default $ZOOMOUT_CLAUDE or claude)")
    p.add_argument("--timeout", type=int, default=300, help="seconds before giving up (default 300)")
    p.set_defaults(fn=_cli_run)
    p = sub.add_parser("hook", help="PostToolUse hook: read the event JSON on stdin")
    p.set_defaults(fn=_cli_hook)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = make_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
