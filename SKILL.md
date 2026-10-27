---
name: zoomout
description: Step back when stuck or about to sink effort. Restates the goal, lists what failed, names the field, describes how top practitioners (as archetypal roles) would approach it, and ends with CONTINUE, PIVOT or SIMPLIFY. Use when the same test fails repeatedly, one file is edited again and again, a command is retried, or progress has stalled, or when a PostToolUse hint says "consider /zoomout".
---

# zoomout

Use this when you are stuck or about to spend a lot more effort on one approach.

1. Restate the real goal and the hard constraints in 5 lines.
2. List what was tried and the most likely reason each attempt failed.
3. Name the field or fields the problem belongs to.
4. For each field, describe how top practitioners would approach it, as archetypal roles such as "a senior SRE at a large-scale operator" or "a compiler engineer". Never name or imitate real people. Cover the standard proven approach, the known pitfall and the simplest thing that could work.
5. Compare the current approach against those views.
6. End with a verdict, exactly one of `CONTINUE`, `PIVOT to <X>` or `SIMPLIFY to <Y>`, plus a line `FIRST STEP:` with the first concrete, checkable step.

Do not run tools or edit files until the verdict is written.

For an answer that is not anchored on the current session's context, run from a shell:

    zoomout run --fresh --goal "<goal>" --tried "<attempt and outcome>" --tried "<another>"

`--dry-run` prints the command without running it.
