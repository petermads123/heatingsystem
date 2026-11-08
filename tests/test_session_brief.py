import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import session_brief
from dev_state import read_brief
from test_dev_state import write_brief, write_round


def test_brief_names_feature_round_step_and_skill(tmp_path: Path) -> None:
    feature = read_brief(write_brief(tmp_path, "feat/csv-export"))
    assert feature is not None
    write_round(
        feature.folder / "r1-writer-core", status="blocked", question="Which delimiter?"
    )
    text = session_brief.brief(tmp_path, feature)
    assert "feat/csv-export" in text
    assert "development/feat/csv-export/" in text
    assert "round 2 CLI: planned" in text
    assert "step 3/9 Implement (blocked)" in text
    assert "Which delimiter?" in text
    assert "/round" in text


@pytest.mark.parametrize(
    ("status", "skill"), [("concept", "/feature"), ("final", "/finish")]
)
def test_brief_names_the_resume_skill(tmp_path: Path, status: str, skill: str) -> None:
    feature = read_brief(write_brief(tmp_path, "feat/x", status=status))
    assert feature is not None
    assert f"Resume with {skill}" in session_brief.brief(tmp_path, feature)


def test_main_is_silent_without_a_live_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_brief(tmp_path, "feat/x", status="review")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    out = io.StringIO()
    with redirect_stdout(out):
        session_brief.main()
    assert out.getvalue() == ""


def test_main_emits_session_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_brief(tmp_path, "feat/x")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    out = io.StringIO()
    with redirect_stdout(out):
        session_brief.main()
    payload = json.loads(out.getvalue())
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "feat/x" in payload["hookSpecificOutput"]["additionalContext"]


def test_brief_reports_a_diagnosis_in_progress(tmp_path: Path) -> None:
    feature = read_brief(
        write_brief(tmp_path, "fix/crash", status="diagnosis", kind="fix", rounds=[])
    )
    assert feature is not None
    made = write_round(
        feature.folder / "diagnosis",
        step=1,
        steps=None,
        status="blocked",
        question="Cannot reproduce: which input?",
    )
    made.rename(made.with_name("state.json"))
    text = session_brief.brief(tmp_path, feature)
    assert "Diagnosis is at step 1/4 Reproduce (blocked)" in text
    assert "Cannot reproduce" in text
    assert "Resume with /fix" in text
