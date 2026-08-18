"""Prompt assembly tests.

The prompt branches on four named grounding states. Every branch has a test,
including the negative assertions that catch the wrong instruction leaking into the
wrong state — an untested branch is how a non-question like "answer this in english"
ends up answered with a refusal.
"""
import pytest

from app.modules.ai_orchestrator.prompts.tutoring_response import (
    GROUNDED_INSTRUCTION,
    GROUNDING_INSTRUCTIONS,
    NOT_NEEDED_INSTRUCTION,
    UNCERTAINTY_INSTRUCTION,
    WEAK_INSTRUCTION,
    build_tutoring_prompt,
)


def test_prompt_renders_all_variables():
    p = build_tutoring_prompt(8, "NCERT", "chunk text", '{"a":1}', "student: hi", grounding="grounded")
    assert "Class 8" in p and "chunk text" in p and '{"a":1}' in p and "student: hi" in p
    assert UNCERTAINTY_INSTRUCTION not in p


@pytest.mark.parametrize(
    "grounding,expected",
    [
        ("grounded", GROUNDED_INSTRUCTION),
        ("weak", WEAK_INSTRUCTION),
        ("empty", UNCERTAINTY_INSTRUCTION),
        ("not_needed", NOT_NEEDED_INSTRUCTION),
    ],
)
def test_each_grounding_state_injects_only_its_own_instruction(grounding, expected):
    p = build_tutoring_prompt(8, "NCERT", "", "", "", grounding=grounding)
    assert expected in p
    for other in GROUNDING_INSTRUCTIONS.values():
        if other is not expected:
            assert other not in p


def test_not_needed_never_mentions_missing_textbook_content():
    """The 2026-08-12 screenshot bug in one assertion: a greeting or a language
    request must never carry the not-in-your-textbook instruction, because the model
    obediently repeats it and the student is refused for saying hello."""
    p = build_tutoring_prompt(8, "NCERT", "", "", "student: pressure kya hai", grounding="not_needed")
    assert UNCERTAINTY_INSTRUCTION not in p
    assert "No matching content was found" not in p


def test_bool_grounding_is_still_accepted():
    """Back-compat guard for a PARTIAL deploy: if orchestrator.py is not replaced but
    this file is, the old positional `no_context` bool must still map to the old
    meaning. Without this, True would fall through to the GROUNDED instruction and
    the model would answer confidently with an empty context — a hallucination
    handed to a child. Delete this test only when the bool branch is deleted."""
    assert UNCERTAINTY_INSTRUCTION in build_tutoring_prompt(8, "NCERT", "", "", "", True)
    assert UNCERTAINTY_INSTRUCTION not in build_tutoring_prompt(8, "NCERT", "", "", "", False)


def test_unknown_grounding_falls_back_to_grounded_not_a_crash():
    p = build_tutoring_prompt(8, "NCERT", "ctx", "", "", grounding="nonsense")
    assert GROUNDED_INSTRUCTION in p
