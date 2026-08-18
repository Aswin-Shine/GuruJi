from pydantic import BaseModel, Field


class OtpRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)


class OtpVerify(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)
    code: str = Field(min_length=4, max_length=8)
    role: str = Field(default="student", pattern="^(student|parent)$")


class TokenResponse(BaseModel):
    access_token: str
    user_id: str
    role: str
