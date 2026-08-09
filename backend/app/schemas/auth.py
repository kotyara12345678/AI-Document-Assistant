from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

PASSWORD_MIN_LEN = 6
PASSWORD_MAX_LEN = 128


class RegisterRequest(BaseModel):
    email: EmailStr = Field(min_length=5, max_length=255)
    password: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)
    password_confirm: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)

    @model_validator(mode="after")
    def passwords_must_match(self) -> "RegisterRequest":
        if self.password != self.password_confirm:
            raise ValueError("Passwords do not match")
        return self


class LoginRequest(BaseModel):
    email: EmailStr = Field(min_length=5, max_length=255)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LEN)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut