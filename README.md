# zoomout

[![tests](https://github.com/decentralizeallengineering/zoomout/actions/workflows/tests.yml/badge.svg)](https://github.com/decentralizeallengineering/zoomout/actions/workflows/tests.yml) ![license](https://img.shields.io/badge/license-Apache--2.0-blue)

Step back and ask what the top experts in the field would do. Agents that are stuck tend to repeat the same fix. zoomout makes them restate the goal, list what failed, name the field, compare against how practitioners in that field work (as archetypal roles, never real people), and end with a verdict: CONTINUE, PIVOT to X, or SIMPLIFY to Y, with a first step. Python standard library only; the only model involved is the Claude Code you already run.

Parts:

- `zoomout.py`: the CLI (`prompt`, `run`, `hook`).
- `.claude/commands/zoomout.md`: the `/zoomout` slash command, for use inside a session.
- `SKILL.md`: the same procedure as a skill.
- A PostToolUse hook (`zoomout hook`) that only suggests `/zoomout`, never blocks. It fires once per trigger kind per session when: the same test fails 3 times; the same file is edited 5 times; a non-test command is retried 3 times in a row; or more than 25 tool calls pass since the last progress signal (a passing test, a successful git commit, or the first edit of a new file).

## Install

    install -m 755 zoomout.py ~/.local/bin/zoomout
    mkdir -p ~/.claude/commands ~/.claude/skills/zoomout
    cp .claude/commands/zoomout.md ~/.claude/commands/
    cp SKILL.md ~/.claude/skills/zoomout/

Add to `~/.claude/settings.json`:

    {"hooks": {"PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 ~/.local/bin/zoomout hook"}]}]}}

## Configure

JSON, first found of `$ZOOMOUT_CONFIG`, `./.claude/zoomout.json`, `$XDG_CONFIG_HOME/zoomout/config.json`, `zoomout.json` next to the script. See the shipped `zoomout.json` for `test_fail_limit`, `edit_limit`, `retry_limit`, `stall_turns` and the test-command regexes. Hook state lives in `$ZOOMOUT_STATE_DIR` (default `~/.cache/zoomout`), one file per session. `ZOOMOUT_OFF=1` disables the hook.

## Example

    zoomout run --fresh --goal "importer must handle nested quotes" \
      --tried "regex parser: breaks on nested quotes" --tried "second regex: slow" \
      --constraints "no new dependencies"

`--fresh` runs headless `claude -p` in an empty temporary working directory with an empty temporary `CLAUDE_CONFIG_DIR`, so the step-back is not anchored on the stuck session's settings, memory or CLAUDE.md files. That clean directory has no saved login, so authenticate through the environment (for example `ANTHROPIC_API_KEY`). `--dry-run` prints the command and runs nothing. `zoomout prompt ...` prints the prompt alone.

## Test

    python3 -m unittest discover -s tests
