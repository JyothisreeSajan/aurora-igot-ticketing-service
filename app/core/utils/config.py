import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()



class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application Identity
    BASE_URL: str = "http://localhost:4020"
    AURORA_APPLICATION_ENVIRONMENT: str = "dev"
    AURORA_APPLICATION_NAME: str = "igot_aurora"

    # LLM Settings
    GOOGLE_API_KEY: str | None = None
    LLM_MODEL: str = "gemini-2.5-flash"

    # ElasticSearch Settings
    ELASTICSEARCH_HOST: str | None = None
    ELASTICSEARCH_USERNAME: str | None = None
    ELASTICSEARCH_PASSWORD: str | None = None
    ELASTICSEARCH_BOT_INTERACTION_INDEX: str = "agent_interaction"
    ELASTICSEARCH_LOGS_INDEX: str = "application_logs"

    # iGOT API Settings
    IGOT_KEY: str | None = None
    IGOT_API_HOST_URL: str = "https://portal.uat.karmayogibharat.net"

    # Zoho API Settings
    ZOHO_CLIENT_ID: str | None = None
    ZOHO_CLIENT_SECRET: str | None = None
    ZOHO_REFRESH_TOKEN: str | None = None
    ZOHO_ORG_ID: str | None = None
    ZOHO_ACCOUNTS_URL: str = "https://accounts.zoho.in"
    ZOHO_DESK_URL: str = "https://desk.zoho.in"
    ZOHO_FROM_ADDRESS: str = "mission.karmayogi@gov.in"  # support inbox for draft replies

    # Kafka Settings
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_TOPIC: str = "resolution_tickets"
    KAFKA_GROUP_ID: str = "aurora_resolution_workers"

    # Feature Flags
    VALIDATE_EMAIL: bool = False
    RESTRICT_TO_EMAIL_CHANNEL: bool = False
    ENABLE_ZOHO_TICKET_UPDATE: bool = True


# Shared singleton settings instance
settings = Settings()

# Ensure GOOGLE_API_KEY is present in os.environ for Google SDKs
if settings.GOOGLE_API_KEY:
    os.environ["GOOGLE_API_KEY"] = settings.GOOGLE_API_KEY


# Backward compatibility module exports
BASE_URL = settings.BASE_URL
AURORA_APPLICATION_ENVIRONMENT = settings.AURORA_APPLICATION_ENVIRONMENT
AURORA_APPLICATION_NAME = settings.AURORA_APPLICATION_NAME
GOOGLE_API_KEY = settings.GOOGLE_API_KEY
LLM_MODEL = settings.LLM_MODEL
ELASTICSEARCH_HOST = settings.ELASTICSEARCH_HOST
ELASTICSEARCH_USERNAME = settings.ELASTICSEARCH_USERNAME
ELASTICSEARCH_PASSWORD = settings.ELASTICSEARCH_PASSWORD
ELASTICSEARCH_BOT_INTERACTION_INDEX = settings.ELASTICSEARCH_BOT_INTERACTION_INDEX
ELASTICSEARCH_LOGS_INDEX = settings.ELASTICSEARCH_LOGS_INDEX
IGOT_KEY = settings.IGOT_KEY
IGOT_API_HOST_URL = settings.IGOT_API_HOST_URL
ZOHO_CLIENT_ID = settings.ZOHO_CLIENT_ID
ZOHO_CLIENT_SECRET = settings.ZOHO_CLIENT_SECRET
ZOHO_REFRESH_TOKEN = settings.ZOHO_REFRESH_TOKEN
ZOHO_ORG_ID = settings.ZOHO_ORG_ID
ZOHO_ACCOUNTS_URL = settings.ZOHO_ACCOUNTS_URL
ZOHO_DESK_URL = settings.ZOHO_DESK_URL
ZOHO_FROM_ADDRESS = settings.ZOHO_FROM_ADDRESS
KAFKA_BOOTSTRAP_SERVERS = settings.KAFKA_BOOTSTRAP_SERVERS
KAFKA_TOPIC = settings.KAFKA_TOPIC
KAFKA_GROUP_ID = settings.KAFKA_GROUP_ID
ENABLE_ZOHO_TICKET_UPDATE = settings.ENABLE_ZOHO_TICKET_UPDATE



