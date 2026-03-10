from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    """
    Централизиран клас за управление на конфигурациите (Environment Variables).
    Наследява BaseSettings от Pydantic, което автоматично чете .env файла 
    и валидира дали всички нужни ключове са налични.
    """
    
    supabase_url: str = Field(..., env="SUPABASE_URL")
    supabase_key: str = Field(..., env="SUPABASE_KEY")
    gemini_api_key: str = Field(..., env="GEMINI_API_KEY")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()