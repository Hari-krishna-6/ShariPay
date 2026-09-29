from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "TrustPay"
    APP_ENV: str = "development"
    DATABASE_URL: str = "postgresql+psycopg://trustpay:trustpay@localhost:5432/trustpay"
    DB_ECHO: bool = False
    API_V1_PREFIX: str = "/api/v1"
    JWT_SECRET: str = "change-this-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ML_MODEL_PATH: Path = Path(__file__).resolve().parents[2] / "ml" / "models" / "fraud_model.joblib"
    DRUNIX_MODE: str = "off"
    DRUNIX_NETWORK_PATH: Path = Path(__file__).resolve().parents[2] / ".." / "drunix" / "drunix-network" / "test-network"
    DRUNIX_CHANNEL: str = "mychannel"
    DRUNIX_CHAINCODE: str = "trustpay"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
