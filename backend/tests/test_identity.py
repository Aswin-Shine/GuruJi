import uuid

from app.modules.identity import service


def test_token_round_trip():
    user_id = uuid.uuid4()
    token = service.issue_token(user_id)
    assert service.verify_token(token) == user_id


def test_tampered_token_rejected():
    token = service.issue_token(uuid.uuid4())
    assert service.verify_token(token[:-4] + "beef") is None
    assert service.verify_token("garbage") is None


def test_logout_revokes_token(client, db):
    """after /logout, the same token is rejected on protected routes."""
    from tests.conftest import make_student

    _, student, token = make_student(db)
    hdr = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/v1/students/{student.id}", headers=hdr).status_code == 200
    assert client.post("/v1/auth/logout", headers=hdr).status_code == 204
    # Same token now dead, even though not expired.
    assert client.get(f"/v1/students/{student.id}", headers=hdr).status_code == 401


def test_revoked_token_fails_verify_with_db(db):
    user_id = uuid.uuid4()
    token = service.issue_token(user_id)
    assert service.verify_token(token, db) == user_id  # valid before revoke
    service.revoke_token(db, token)
    assert service.verify_token(token, db) is None       # dead after revoke
