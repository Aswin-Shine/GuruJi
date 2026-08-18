from tests.conftest import make_parent, make_student


def test_student_cannot_read_other_student(client, db):
    """IDOR check: authenticated student A must not fetch student B by UUID."""
    _, student_a, token_a = make_student(db)
    _, student_b, _ = make_student(db)
    resp = client.get(f"/v1/students/{student_b.id}", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 403
    resp = client.get(f"/v1/students/{student_a.id}", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200


def test_unauthenticated_rejected(client, db):
    _, student, _ = make_student(db)
    assert client.get(f"/v1/students/{student.id}").status_code == 401


def test_unlinked_parent_rejected(client, db):
    _, student, _ = make_student(db)
    parent_user, parent_token = make_parent(db)
    resp = client.get(f"/v1/students/{student.id}/summary", headers={"Authorization": f"Bearer {parent_token}"})
    assert resp.status_code == 403


def test_parent_link_flow_via_real_endpoints(client, db):
    """student invites parent -> PIN -> parent verifies -> summary works.
    No raw SQL insert anywhere; the whole path goes through the real endpoints."""
    _, student, student_token = make_student(db)
    parent_user, parent_token = make_parent(db)
    s_hdr = {"Authorization": f"Bearer {student_token}"}
    p_hdr = {"Authorization": f"Bearer {parent_token}"}

    # 1. Student invites parent by phone -> gets a PIN, link starts UNVERIFIED.
    resp = client.post(
        f"/v1/students/{student.id}/link-parent",
        json={"parent_phone_number": parent_user.phone_number},
        headers=s_hdr,
    )
    assert resp.status_code == 201, resp.text
    pin = resp.json()["link_pin"]
    assert resp.json()["verified"] is False

    # 2. Summary still blocked before verification.
    assert client.get(f"/v1/students/{student.id}/summary", headers=p_hdr).status_code == 403

    # 3. Wrong PIN rejected.
    assert client.post(
        f"/v1/students/{student.id}/verify-parent-link", json={"link_pin": "999999"}, headers=p_hdr
    ).status_code == 403

    # 4. Correct PIN verifies the link.
    assert client.post(
        f"/v1/students/{student.id}/verify-parent-link", json={"link_pin": pin}, headers=p_hdr
    ).status_code == 204

    # 5. Summary now succeeds.
    resp = client.get(f"/v1/students/{student.id}/summary", headers=p_hdr)
    assert resp.status_code == 200
    assert "total_messages" in resp.json()


def test_only_owning_student_can_invite_parent(client, db):
    """A student cannot create a parent link on someone else's profile (IDOR)."""
    _, student_a, token_a = make_student(db)
    _, student_b, _ = make_student(db)
    parent_user, _ = make_parent(db)
    resp = client.post(
        f"/v1/students/{student_b.id}/link-parent",
        json={"parent_phone_number": parent_user.phone_number},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403


def test_pin_brute_force_locks_out_and_requires_reinvite(client, db):
    """the PIN space is 6 digits and the PIN is deterministic per link row,
    so unlimited guessing guarantees a break. After 5 wrong attempts the link is
    deleted — the previously-correct PIN is dead (404), and only a fresh invite
    (new row -> new PIN) works."""
    _, student, student_token = make_student(db)
    parent_user, parent_token = make_parent(db)
    s_hdr = {"Authorization": f"Bearer {student_token}"}
    p_hdr = {"Authorization": f"Bearer {parent_token}"}

    resp = client.post(
        f"/v1/students/{student.id}/link-parent",
        json={"parent_phone_number": parent_user.phone_number},
        headers=s_hdr,
    )
    real_pin = resp.json()["link_pin"]

    wrong = "000000" if real_pin != "000000" else "000001"
    for _ in range(5):
        r = client.post(
            f"/v1/students/{student.id}/verify-parent-link", json={"link_pin": wrong}, headers=p_hdr
        )
        assert r.status_code == 403

    # 6th attempt with the WOULD-BE-CORRECT pin: link is gone -> 404, not 204.
    r = client.post(
        f"/v1/students/{student.id}/verify-parent-link", json={"link_pin": real_pin}, headers=p_hdr
    )
    assert r.status_code == 404

    # Re-invite: new row, DIFFERENT pin, and it verifies.
    resp2 = client.post(
        f"/v1/students/{student.id}/link-parent",
        json={"parent_phone_number": parent_user.phone_number},
        headers=s_hdr,
    )
    new_pin = resp2.json()["link_pin"]
    assert new_pin != real_pin
    r = client.post(
        f"/v1/students/{student.id}/verify-parent-link", json={"link_pin": new_pin}, headers=p_hdr
    )
    assert r.status_code == 204


def test_students_me_404_for_account_without_profile(client, db):
    """The ONLY signal that should open onboarding.

    Auth routes on this: 404 means genuinely new, so show "Which class are you
    in?". Anything else must not, because asking a registered student their class
    changes nothing (see the 409 test below) and a browser with an empty cache is
    not evidence of a new account.
    """
    from app.modules.identity import service as identity

    user = identity.get_or_create_user(db, "+919000000101", "student")
    token = identity.issue_token(user.id)
    resp = client.get("/v1/students/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_students_me_resolves_returning_student_on_a_fresh_browser(client, db):
    """A returning student must be identifiable from the token alone.

    This is what lets sign-in skip onboarding: the account, not this browser's
    cached studentId, decides whether a profile exists.
    """
    _, student, token = make_student(db, grade=7)
    resp = client.get("/v1/students/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(student.id)
    assert body["grade"] == 7


def test_reonboarding_is_rejected_and_cannot_change_grade(client, db):
    """Answering the class question twice must not overwrite an existing profile.

    This is why the question is skipped rather than merely tolerated: a second
    answer is discarded, so asking it would be friction with a dead end behind it.
    Changing class per-chat is the class pill's job, not onboarding's.
    """
    _, student, token = make_student(db, grade=7)
    resp = client.post("/v1/students", json={"grade": 9}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409
    after = client.get("/v1/students/me", headers={"Authorization": f"Bearer {token}"})
    assert after.json()["grade"] == 7
