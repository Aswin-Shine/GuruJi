from app.modules.memory import service
from tests.conftest import make_student


def test_memory_set_and_get(db):
    _, student, _ = make_student(db)
    assert service.get_summary(db, student.id) == {}
    service.set_summary(db, student.id, {"struggle_topics": ["fractions"]})
    assert service.get_summary(db, student.id)["struggle_topics"] == ["fractions"]


def test_regenerate_skips_paid_call_when_cap_tripped(db, monkeypatch):
    """a tripped daily spend cap must stop memory regeneration from making
    any paid llm.chat() call."""
    from app.modules.ai_orchestrator.llm import SpendCapExceeded
    from unittest.mock import patch

    _, student, _ = make_student(db)
    with patch.object(service.llm, "check_spend_cap", side_effect=SpendCapExceeded), \
         patch.object(service.llm, "chat") as mock_chat:
        # regenerate() no longer takes the request session — it opens its own.
        service.regenerate(student.id, 8, "student: hi\nassistant: hello")
    mock_chat.assert_not_called()
