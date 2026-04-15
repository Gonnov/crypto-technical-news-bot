from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str
    telegram_bot_token: str
    telegram_chat_id: int

    data_dir: str = "./data"
    schedule_tz: str = "Asia/Bangkok"
    schedule_hour: int = 8
    schedule_minute: int = 0
    gemini_model: str = "gemini-2.5-flash-lite"


settings = Settings()
