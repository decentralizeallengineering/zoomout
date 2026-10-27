#!/usr/bin/env python3
"""zoomout: step back and ask what the top experts in the field would do.

Subcommands:
  zoomout prompt --goal G --tried T [--tried T ...]   print the step-back prompt
  zoomout run    --goal G --tried T ... [--fresh] [--dry-run]
                                                      run it through headless `claude -p`
  zoomout hook                                        PostToolUse/PostToolUseFailure hook: suggest
                                                      /zoomout when a trigger fires (never blocks)

Config: JSON, first found of $ZOOMOUT_CONFIG, ./.claude/zoomout.json,
$XDG_CONFIG_HOME/zoomout/config.json, zoomout.json next to this script.
Hook state: $ZOOMOUT_STATE_DIR (default ~/.cache/zoomout), one file per session.
Kill switch for the hook: ZOOMOUT_OFF=1. The hook fails open.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

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

PROMPT = """You are asked to zoom out. An engineering agent is stuck or about to sink more effort into an approach. \
Answer from your own knowledge. Do not use tools, do not read files, do not write code. \
Describe experts only as archetypal roles (for example "a senior SRE at a large-scale operator" or \
"a compiler engineer"); never name or imitate real people.

GOAL (as given): {goal}
HARD CONSTRAINTS (as given): {constraints}
WHAT WAS TRIED (as given):
{tried}
{fields_line}
Produce exactly these sections, in this order:

1. GOAL AND CONSTRAINTS: restate the real goal and the hard constraints in 5 lines, no more.
2. TRIED AND WHY IT FAILED: one line per attempt, with the most likely reason it failed.
3. FIELDS: name the field or fields this problem belongs to. Often the real field is not the one the agent assumed.
4. EXPERT VIEW: for each field, as an archetypal role, give three short items: \
the standard proven approach, the known pitfall, and the simplest thing that could work.
5. COMPARISON: compare the current approach against those views. Say where it matches, where it diverges, \
and what the experts would call a sign of sunk effort.
6. VERDICT: exactly one of CONTINUE, PIVOT to <X>, or SIMPLIFY to <Y>, then a line starting \
"FIRST STEP:" with the first concrete, checkable step.
"""


def load_config():
    here = os.path.dirname(os.path.abspath(__file__))
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    cfg = dict(DEFAULTS)
    for path in (os.environ.get("ZOOMOUT_CONFIG"), os.path.join(".claude", "zoomout.json"),
                 os.path.join(xdg, "zoomout", "config.json"), os.path.join(here, "zoomout.json")):
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    cfg.update(json.load(fh))
            except (OSError, ValueError):
                continue
            break
    return cfg


# ------------------------------------------------------------------ prompt / run
def build_prompt(goal, tried, constraints=None, fields=None):
    tried_text = "\n".join("- " + t for t in tried) if tried else "- (nothing listed)"
    fields_line = ("Fields suggested by the caller (confirm or correct them): " + ", ".join(fields) + "\n"
                   if fields else "")
    return PROMPT.format(goal=goal, constraints=constraints or "(none stated; infer and flag as inferred)",
                         tried=tried_text, fields_line=fields_line)


def build_command(prompt, claude_bin, model=None):
    cmd = [claude_bin, "-p", prompt]
    if model:
        cmd += ["--model", model]
    return cmd


def cmd_prompt(args):
    sys.stdout.write(build_prompt(args.goal, args.tried, args.constraints, args.field))
    return 0


def cmd_run(args):
    prompt = build_prompt(args.goal, args.tried, args.constraints, args.field)
    claude_bin = args.claude_bin or os.environ.get("ZOOMOUT_CLAUDE") or "claude"
    cmd = build_command(prompt, claude_bin, args.model)
    if args.dry_run:
        prefix = "cd <new empty temp dir> && CLAUDE_CONFIG_DIR=<new empty temp dir> " if args.fresh else ""
        sys.stdout.write(prefix + " ".join(shlex.quote(c) for c in cmd) + "\n")
        return 0
    env = dict(os.environ)
    tmp = None
    try:
        if args.fresh:
            tmp = tempfile.mkdtemp(prefix="zoomout-")
            env["CLAUDE_CONFIG_DIR"] = tmp
        proc = subprocess.run(cmd, env=env, cwd=tmp, capture_output=True, text=True,
                              timeout=args.timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"zoomout: could not run {claude_bin}: {exc}\n")
        return 2
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode:
        sys.stderr.write(proc.stderr)
    return proc.returncode


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


def _state_path(event):
    root = os.environ.get("ZOOMOUT_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".cache", "zoomout")
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(event.get("session_id") or "default")) or "default"
    return root, os.path.join(root, sid + ".json")


def cmd_hook(_args):
    if os.environ.get("ZOOMOUT_OFF"):
        return 0
    try:
        event = json.load(sys.stdin)
        root, path = _state_path(event)
        state = new_state()
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                state.update(json.load(fh))
        notes = process(event, state, load_config())
        os.makedirs(root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        if notes:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {  # must echo the event name or it is dropped
                "hookEventName": event.get("hook_event_name") or "PostToolUse", "additionalContext": "\n".join(notes)}}))
    except Exception:  # noqa: BLE001  suggestions only: never disturb the session
        return 0
    return 0


# ------------------------------------------------------------------ cli
def make_parser():
    ap = argparse.ArgumentParser(prog="zoomout", description="Step back and ask what top experts would do.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--goal", required=True, help="the real goal, one or two sentences")
        p.add_argument("--tried", action="append", default=[], help="an attempt and its outcome (repeatable)")
        p.add_argument("--constraints", help="hard constraints, if any")
        p.add_argument("--field", action="append", help="suggested field of expertise (repeatable)")

    p = sub.add_parser("prompt", help="print the step-back prompt")
    common(p)
    p.set_defaults(fn=cmd_prompt)
    p = sub.add_parser("run", help="run the step-back through headless claude -p")
    common(p)
    p.add_argument("--fresh", action="store_true",
                   help="use an empty temp CLAUDE_CONFIG_DIR and working dir, so the answer is not anchored "
                        "on the stuck session's settings, memory or CLAUDE.md files")
    p.add_argument("--dry-run", action="store_true", help="print the command instead of running it")
    p.add_argument("--model", help="model name passed to claude --model")
    p.add_argument("--claude-bin", help="claude executable (default $ZOOMOUT_CLAUDE or claude)")
    p.add_argument("--timeout", type=int, default=300, help="seconds before giving up (default 300)")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("hook", help="PostToolUse/PostToolUseFailure hook: read event JSON on stdin")
    p.set_defaults(fn=cmd_hook)
    return ap


def main(argv=None):
    args = make_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
