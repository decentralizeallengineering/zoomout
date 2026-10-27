---
description: Step back and ask what the top experts in the field would do, then give a verdict
argument-hint: [what you are stuck on]
---

Stop working on the current approach and zoom out. Do not run tools or edit files until the verdict is written. Extra context from the user: $ARGUMENTS

Write these six sections, concise, in this order.

1. GOAL AND CONSTRAINTS: restate the real goal and the hard constraints in 5 lines, no more.
2. TRIED AND WHY IT FAILED: one line per attempt, with the most likely reason it failed. Include test runs and retries.
3. FIELDS: name the field or fields the problem belongs to. The real field is often not the one you assumed.
4. EXPERT VIEW: for each field, describe how top practitioners would approach it, as an archetypal role (for example "a senior SRE at a large-scale operator" or "a compiler engineer"). Never name or imitate real people. Give three short items: the standard proven approach, the known pitfall, and the simplest thing that could work.
5. COMPARISON: compare the current approach against those views. Where does it match, where does it diverge, and what would they call sunk effort?
6. VERDICT: exactly one of `CONTINUE`, `PIVOT to <X>`, or `SIMPLIFY to <Y>`, then a line `FIRST STEP:` with the first concrete, checkable step.

For an answer not anchored on this session, the user can run `zoomout run --fresh --goal "..." --tried "..."`.
