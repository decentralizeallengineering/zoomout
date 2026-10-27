# zoomout

[![tests](https://github.com/decentralizeallengineering/zoomout/actions/workflows/tests.yml/badge.svg)](https://github.com/decentralizeallengineering/zoomout/actions/workflows/tests.yml) ![license](https://img.shields.io/badge/license-Apache--2.0-blue)

Step back and ask what the top experts in the field would do. Agents that are stuck tend to repeat the same fix. zoomout makes them restate the goal, list what failed, quote the key fact, name the field, compare against how practitioners in that field work (as archetypal roles, never real people), and end with a verdict: CONTINUE, PIVOT to X, or SIMPLIFY to Y, with a first step. Python standard library only; the only model involved is the Claude Code you already run.

## Install

    git clone --depth 1 https://github.com/decentralizeallengineering/zoomout && python3 zoomout/zoomout.py install

This copies the `/zoomout` command, the skill and `zoomout.py` into `${CLAUDE_CONFIG_DIR:-~/.claude}` (`--project DIR` puts them in `DIR/.claude` instead); nothing is added to your PATH. On Windows use `py` for `python3`. Optional hook that suggests `/zoomout` when the agent looks stuck:

    python3 zoomout/zoomout.py init --print

It prints the settings snippet to merge into `settings.json`; zoomout never edits settings. The snippet uses the exec form (`command` plus `args`), so no shell parses it, and registers both `PostToolUse` and `PostToolUseFailure`, because Claude Code reports a failing command only as `PostToolUseFailure`.

## Operations

Each is a plain function in `zoomout.py` that returns `{"ok", "changed", "warnings", "details"}` and never raises; `SCHEMAS[name]` is its argument schema, for calling it as an LLM tool.

- `install`: copy the command, the skill and the script into `~/.claude` or a project.
- `init`: return the hook settings snippet (`zoomout.py init --print`); never edits settings.
- `prompt`: build the step-back prompt from a goal, attempts and constraints.
- `run`: answer the prompt with headless `claude -p` in an empty temp dir and an empty `CLAUDE_CONFIG_DIR` (`zoomout.py run --fresh`).
- `hook`: the hook; suggests `/zoomout` once per trigger kind per session, never blocks.

`run --fresh` is the only place zoomout starts a subprocess (`_claude_fresh`). zoomout makes no network calls of its own and sends no telemetry. The clean config dir has no saved login, so authenticate through the environment (for example `ANTHROPIC_API_KEY`). `--dry-run` prints the command and runs nothing.

    python3 ~/.claude/skills/zoomout/zoomout.py run --fresh --goal "importer must handle nested quotes" \
      --tried "regex parser: breaks on nested quotes" --tried "second regex: slow" \
      --constraints "no new dependencies"

## Configure

The hook fires when: the same test fails 3 times; the same file is edited 5 times; a non-test command is retried 3 times in a row; or more than 25 tool calls pass since the last progress signal (a passing test, a successful git commit, or the first edit of a new file). Override any of `test_fail_limit`, `edit_limit`, `retry_limit`, `stall_turns` or the regex lists in `DEFAULTS` with a JSON file: the first found of `$ZOOMOUT_CONFIG`, `./.claude/zoomout.json`, `zoomout.json` next to the script. Hook state lives in `$ZOOMOUT_STATE_DIR` (default `~/.cache/zoomout`), one file per session. `ZOOMOUT_OFF=1` disables the hook. A config value of the wrong type is ignored.

## Known limits

- Parallel tool calls run the hook concurrently; each call saves the session state atomically, but the last writer wins, so a count can come out low and a suggestion can come late.
- UNVERIFIED on Windows: with npm's `claude.cmd` shim, `run --fresh --timeout` may stop only `cmd.exe` and leave `claude` running; the native `claude.exe` is not affected.

## Test

    pip install pytest jsonschema && python -m pytest -q
