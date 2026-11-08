"""SessionStart hook: tell a new session which feature is in flight and how to resume it.

Reads the live `brief.json` and its current `round.json` and injects one short
paragraph into the session's context: the feature, its rounds, the step the
current round is on, and the skill that picks the work up. Silent when nothing
is being built, which includes every checkout of `main`. Stdlib only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dev_state import Brief, active_brief, diagnosis_state, round_state

RESUME_SKILL = {
    "diagnosis": "/fix",
    "concept": "/feature",
    "rounds": "/round",
    "final": "/finish",
}


def brief(project_dir: Path, feature: Brief) -> str:
    """Describe one live feature's state.

    Args:
        project_dir: Repository root.
        feature: The live feature.

    Returns:
        A short paragraph for the session's context.
    """
    folder = feature.folder.relative_to(project_dir).as_posix()
    lines = [
        f"Feature in flight: {feature.title or feature.feature} on {feature.branch}"
        f" (status {feature.status}, bump {feature.bump}). State: {folder}/.",
    ]
    diagnosis = diagnosis_state(feature)
    if diagnosis is not None and feature.status == "diagnosis":
        lines.append(
            f"Diagnosis is at step {diagnosis.step}/{len(diagnosis.steps)}"
            f" {diagnosis.step_name} ({diagnosis.status})."
        )
        if diagnosis.question:
            lines.append(f"Open question: {diagnosis.question}")
    for entry in feature.rounds:
        lines.append(f"- round {entry.number} {entry.name}: {entry.status}")
    state = round_state(feature, feature.current_round)
    if state is not None and feature.status == "rounds":
        lines.append(
            f"Round {state.round} is at step {state.step}/{len(state.steps)}"
            f" {state.step_name} ({state.status})."
        )
        if state.question:
            lines.append(f"Open question: {state.question}")
    skill = RESUME_SKILL.get(feature.status, "/feature")
    lines.append(f"Resume with {skill}; it reads this state and continues.")
    return "\n".join(lines)


def main() -> None:
    """Entry point: emit the brief as session context, or nothing."""
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    feature = active_brief(project_dir)
    if feature is None:
        return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": brief(project_dir, feature),
                }
            }
        )
    )


if __name__ == "__main__":
    main()
