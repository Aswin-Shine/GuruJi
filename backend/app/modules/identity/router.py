from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import DEV_OTP_BYPASS
from app.db.session import get_db
from app.modules.identity import service
from app.modules.identity.dependencies import get_current_user
from app.modules.identity.schemas import OtpRequest, OtpVerify, TokenResponse

router = APIRouter(prefix="/v1/auth", tags=["auth"])

DEV_OTP_CODE = "000000"


@router.post("/otp/request")
def otp_request(body: OtpRequest) -> dict:
    # Dev-mode bypass: no real SMS/WhatsApp delivery in Phase 1. Real OTP delivery is Phase 2.
    if not DEV_OTP_BYPASS:
        raise HTTPException(status_code=501, detail="Real OTP delivery not built in Phase 1")
    return {"status": "sent", "dev_hint": f"use code {DEV_OTP_CODE}"}


@router.post("/otp/verify", response_model=TokenResponse)
def otp_verify(body: OtpVerify, db: Session = Depends(get_db)) -> TokenResponse:
    if not DEV_OTP_BYPASS or body.code != DEV_OTP_CODE:
        raise HTTPException(status_code=401, detail="Invalid OTP")
    user = service.get_or_create_user(db, body.phone_number, body.role)
    return TokenResponse(access_token=service.issue_token(user.id), user_id=str(user.id), role=user.role)


@router.post("/logout", status_code=204)
def logout(request: Request, _=Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    """Revoke the presented token server-side — it is unusable immediately after,
    even though it hasn't expired. get_current_user guarantees the token was valid."""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    service.revoke_token(db, token)
