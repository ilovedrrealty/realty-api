from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    resend_api_key: str = ""
    email_from: str = "noreply@ilovedrrealty.com"
    landing_url: str = "http://localhost:3000"
    logo_url: str = ""  # override with CDN URL; falls back to landing_url/iLoveDRRealty_Dark.png
    allowed_origins: str = "http://localhost:3000,http://localhost:5174"
    environment: str = "development"
    cookie_domain: str = ""  # set to ".ilovedrrealty.com" in production
    port: int = 8000
    supabase_url: str = ""
    supabase_service_key: str = ""
    storage_bucket: str = "listing-images"
    ghl_api_key: str = ""
    ghl_location_id: str = ""
    ghl_enabled: bool = False
    lead_delivery_enabled: bool = False
    property_inquiry_require_consent: bool = False
    ghl_webhook_secret: str = ""
    notify_email: str = ""  # set to ilovedrrealty@gmail.com in Railway
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_webhook_id: str = ""
    paypal_mode: str = "sandbox"  # sandbox | live
    platform_fee_pct: float = 0.20  # 20% platform cut taken before owner payout

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
