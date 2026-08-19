from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

PASSWORD_MIN_LEN = 6
PASSWORD_MAX_LEN = 128
# bcrypt only uses the first 72 bytes of the password; anything longer would be
# silently truncated and produce a hash that matches the wrong prefix.
BCRYPT_MAX_BYTES = 72


class RegisterRequest(BaseModel):
    email: EmailStr = Field(min_length=5, max_length=255)
    password: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)
    password_confirm: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)

    @model_validator(mode="after")
    def passwords_must_match(self) -> "RegisterRequest":
        if self.password != self.password_confirm:
            raise ValueError("Passwords do not match")
        if len(self.password.encode("utf-8")) > BCRYPT_MAX_BYTES:
            raise ValueError(
                f"Password must not exceed {BCRYPT_MAX_BYTES} bytes in UTF-8 encoding"
            )
        return self


class LoginRequest(BaseModel):
    email: EmailStr = Field(min_length=5, max_length=255)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LEN)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LEN)
    new_password: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)
    password_confirm: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)

    @model_validator(mode="after")
    def passwords_must_match(self) -> "PasswordChangeRequest":
        if self.new_password != self.password_confirm:
            raise ValueError("Passwords do not match")
        if len(self.new_password.encode("utf-8")) > BCRYPT_MAX_BYTES:
            raise ValueError(
                f"Password must not exceed {BCRYPT_MAX_BYTES} bytes in UTF-8 encoding"
            )
        return self


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str = "user"
    created_at: datetime
    avatar_url: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LEN)
    new_password: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)
    new_password_confirm: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)

    @model_validator(mode="after")
    def passwords_must_match(self) -> "ChangePasswordRequest":
        if self.new_password != self.new_password_confirm:
            raise ValueError("Passwords do not match")
        if len(self.new_password.encode("utf-8")) > BCRYPT_MAX_BYTES:
            raise ValueError(
                f"Password must not exceed {BCRYPT_MAX_BYTES} bytes in UTF-8 encoding"
            )
        return self


class UpdateProfileRequest(BaseModel):
    # Optional raise avatar data URL, e.g. "data:image/png;base64,...." Its
    # value shadows a previous one, or clears it when None is passed. Must
    # start with the data:image/ prefix so only real raster images are kept.
    avatar_url: str | None = Field(default=None, max_length=2_000_000)

    @model_validator(mode="after")
    def avatar_url_must_be_a_data_image(self) -> "UpdateProfileRequest":
        if self.avatar_url is not None and not self.avatar_url.strip().startswith(
            "data:image/"
        ):
            raise ValueError("avatar_url must be a data:image/... URL")
        return self


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UsageStatsOut(BaseModel):
    total_tokens: int
    tokens_today: int
    tokens_7d: int
    tokens_30d: int
    requests: int