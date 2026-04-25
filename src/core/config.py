from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str
    azure_devops_org: str
    azure_devops_pat: str
    azure_devops_projects: list[str] = ["Cantera", "Progresol"]

    claude_model: str = "claude-sonnet-4-6"
    index_path: str = "data/index.json"
    api_key: str

    openai_api_key: str
    qdrant_url: str = "http://localhost:6333"

    # Optional: "username:password" configured in Azure DevOps Service Hook.
    # If set, every webhook request must include matching Basic Auth header.
    webhook_secret: str | None = None


settings = Settings()
