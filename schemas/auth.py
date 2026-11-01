from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Literal
import uuid


class RegisterRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=30)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RegisterResponse(BaseModel):
    email: str
    message: str = "Verification code sent"


class VerifyRequest(BaseModel):
    email: EmailStr
    code: str
    locale: Literal["en", "es"] = "en"


class ResendCodeRequest(BaseModel):
    email: EmailStr


class TokenResponse(BaseModel):
    expires_in: int = 3600


class CreateAdminUserBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: str = 'buyer'


class GoogleAuthRequest(BaseModel):
    access_token: str
    locale: Literal["en", "es"] = "en"


class UpdateProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=30)
    calendly_url: Optional[str] = Field(default=None, max_length=500)
    paypal_email: Optional[str] = Field(default=None, max_length=255)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class SetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
