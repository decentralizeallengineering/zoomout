---
description: Step back from a stuck approach and get a verdict of CONTINUE, PIVOT or SIMPLIFY
argument-hint: "[what you are stuck on]"
---

Zoom out on the current task. Extra context from the user: $ARGUMENTS

Stop. Do not run tools or edit files until the verdict is written. Keep the whole answer under 35 lines.

1. GOAL AND CONSTRAINTS: the outcome actually needed (not the current fix) and the hard constraints, at most 5 lines.
2. TRIED AND WHY IT FAILED: one line per attempt with the most likely reason it failed. Then one line "KEY FACT: <quote>" quoting the single observed fact from the session (an error, log line, header, number or search hit) that best explains the failures, or "KEY FACT: none".
3. FIELDS: the field or fields the problem really belongs to. The symptom shows where the problem appears, not always where it lives.
4. EXPERT VIEW: for each field, an archetypal practitioner (for example "a senior SRE at a large-scale operator" or "a compiler engineer"; never a real or named person) and three short items: the standard proven approach, the known pitfall, and the simplest thing that could work.
5. COMPARISON: where the current approach matches those views, where it diverges, and which effort is sunk.
6. VERDICT: choose exactly one.
   CONTINUE: keep the current approach. It is the standard one and the evidence shows it converging.
   PIVOT to <X>: replace the current approach. It works in the wrong place, on the wrong cause, or with a method that cannot converge. X is where or how to work instead.
   SIMPLIFY to <Y>: keep the target but shrink the solution, which is bigger than the problem. Y deletes or skips something, cuts scope, or uses an existing standard tool instead of hand-built code.
   Do not abandon converging work because the session feels long. X or Y must name the specific thing (file, service, query, setting, library or test), never "a different approach".

End with exactly these two lines and nothing after them:
VERDICT: <CONTINUE, or PIVOT to X, or SIMPLIFY to Y>
FIRST STEP: <one concrete action that names a specific file, command or setting from this session, and the result that would confirm the verdict>
