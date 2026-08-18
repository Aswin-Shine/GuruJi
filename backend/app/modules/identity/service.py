"""Token = HMAC-signed 'user_id:jti:expiry:sig'. Stdlib only — no JWT dependency.
The jti (per-token id) lets a specific token be revoked server-side (logout / leak).
TTL shortened from 30d to 7d to bound the damage window of any token we can't reach."""
import datetime as dt
import hashlib
import hmac
import time
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import SECRET_KEY
from app.modules.identity.models import User

TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7d; refresh-token rotation is a Phase 2 item


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(user_id: uuid.UUID) -> str:
    jti = uuid.uuid4().hex
    expiry = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"{user_id}:{jti}:{expiry}"
    return f"{payload}:{_sign(payload)}"


def _parse(token: str) -> tuple[uuid.UUID, str, int] | None:
    parts = token.split(":")
    if len(parts) != 4:
        return None
    user_id_str, jti, expiry_str, sig = parts
    payload = f"{user_id_str}:{jti}:{expiry_str}"
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        return uuid.UUID(user_id_str), jti, int(expiry_str)
    except ValueError:
        return None


def verify_token(token: str, db: Session | None = None) -> uuid.UUID | None:
    parsed = _parse(token)
    if parsed is None:
        return None
    user_id, jti, expiry = parsed
    if expiry < time.time():
        return None
    # Revocation check requires a DB session; callers that resolve a real request
    # always pass one. Signature+expiry alone still hold when db is None (e.g. unit tests).
    if db is not None:
        revoked = db.execute(text("SELECT 1 FROM revoked_tokens WHERE jti = :j"), {"j": jti}).first()
        if revoked is not None:
            return None
    return user_id


def revoke_token(db: Session, token: str) -> bool:
    """Invalidate a specific token by recording its jti. Idempotent. Returns False
    if the token doesn't parse (nothing to revoke)."""
    parsed = _parse(token)
    if parsed is None:
        return False
    _, jti, expiry = parsed
    expires_at = dt.datetime.fromtimestamp(expiry, tz=dt.timezone.utc)
    db.execute(
        text(
            "INSERT INTO revoked_tokens (jti, expires_at) VALUES (:j, :e) "
            "ON CONFLICT (jti) DO NOTHING"
        ),
        {"j": jti, "e": expires_at},
    )
    db.commit()
    return True


def get_or_create_user(db: Session, phone_number: str, role: str) -> User:
    user = db.execute(select(User).where(User.phone_number == phone_number)).scalar_one_or_none()
    if user is None:
        user = User(phone_number=phone_number, role=role)
        db.add(user)
        db.commit()
    return user


def get_user(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)
