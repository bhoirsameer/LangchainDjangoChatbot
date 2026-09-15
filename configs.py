from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8005

    database_url: str
    groq_api_key: str
    openai_api_key: str
    langchain: str
    hf_token: str

    model_name: str = "llama-3.1-70b-versatile"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

if __name__ == "__main__":
    print(settings.model_dump())