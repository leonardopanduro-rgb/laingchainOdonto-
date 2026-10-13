from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)
    odonto_backend_url: str = "http://127.0.0.1:8000"
    odonto_inbound_token: SecretStr
    odonto_agent_token: SecretStr
    reception_api_key: SecretStr
    google_api_key: SecretStr
    reception_model: str = "gemini-2.5-flash"
    reception_db: str = "reception.db"
    chatwoot_url: str | None = None
    chatwoot_token: SecretStr = SecretStr("")
    chatwoot_webhook_secret: SecretStr = SecretStr("")
    chatwoot_account_id: int = Field(default=1, ge=1)
    chatwoot_inbox_id: int = Field(default=1, ge=1)
    chatwoot_team_id: int | None = Field(default=None, ge=1)
    odonto_channel_external_id: str = ""
    odonto_delivery_token: SecretStr = SecretStr("")
    odonto_operator_token: SecretStr = SecretStr("")
    reception_buffer_seconds: int = Field(default=30, ge=0, le=120)
    media_allowed_origins: list[str] = []
