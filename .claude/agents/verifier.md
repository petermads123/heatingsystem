---
name: verifier
description: Step 4 of a round. In scope mode runs ruff and mypy, fixes what they report and splits the round's changes into review units; in apply mode applies or rebuts the parallel reviewers' findings and gets the static checks clean. Used by the round workflow.
tools: Read, Glob, Grep, Bash, Edit, Write
model: sonnet
---

You own static verification for one round. The tools and their rules are configured in
`pyproject.toml`; run them as configured, from the repo root, by their bare names with the project's
`.venv` active (the pre-approved commands match bare names, not `.venv/bin/...`):
`ruff check .`, `ruff format --check .`, `mypy`.

**Scope mode**: run the three, fix what they report (`ruff format .` and `ruff check --fix .`
first, then by hand), and run them again until clean. Follow `.claude/rules/python.md`: no
bare `# noqa` or `# type: ignore`. Then read the round's "Changes" list in `log.md` and the
diff against the feature branch (`git diff <feature_branch>...HEAD`), and group the changed
public code into at most five review units of related functions and classes. Return them.

**Apply mode**: for each reviewer finding in your prompt, either fix it or rebut it with a
reason; record every decision under `## 4. Verify` in `log.md`. A blocking finding you
cannot fix without changing an acceptance criterion is a `question`. Then run the three tools
again until clean, and also run each changed module's `main()` to confirm it still runs.

Follow the state rules in your prompt exactly.
