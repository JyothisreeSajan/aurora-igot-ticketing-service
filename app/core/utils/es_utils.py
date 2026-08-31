"""
app/core/utils/es_utils.py
---------------------------
ElasticSearch connection manager (singleton) for the iGOT Aurora agent.

Provides a lazily-initialised ESManager singleton (`es_manager`) that:
  - Connects to ES using credentials from app/core/utils/config.py
  - Prefixes index names as {app_name}_{env}_{index_base}
  - Exposes `bot_index` (ticket store) and `logs_index` (application logs)
  - Degrades gracefully when ELASTICSEARCH_HOST is not configured

Usage:
    from app.core.utils.es_utils import es_manager
    es_manager.client.index(index=es_manager.bot_index, document={...})
"""
import logging

from datetime import datetime

from elasticsearch import Elasticsearch

from app.core.utils.config import (
    AURORA_APPLICATION_ENVIRONMENT,
    AURORA_APPLICATION_NAME,
    ELASTICSEARCH_BOT_INTERACTION_INDEX,
    ELASTICSEARCH_HOST,
    ELASTICSEARCH_LOGS_INDEX,
    ELASTICSEARCH_PASSWORD,
    ELASTICSEARCH_USERNAME,
)

logger = logging.getLogger(__name__)

class ESManager:
    def __init__(self):
        self.client = None
        self._setup_client()
        
        # Define prefixed index names
        self.bot_index = f"{AURORA_APPLICATION_NAME}_{AURORA_APPLICATION_ENVIRONMENT}_{ELASTICSEARCH_BOT_INTERACTION_INDEX}"
        self.logs_index = f"{AURORA_APPLICATION_NAME}_{AURORA_APPLICATION_ENVIRONMENT}_{ELASTICSEARCH_LOGS_INDEX}"

    def _setup_client(self):
        if not ELASTICSEARCH_HOST:
            logger.warning("ELASTICSEARCH_HOST not configured. ES features disabled.")
            return

        try:
            self.client = Elasticsearch(
                ELASTICSEARCH_HOST,
                basic_auth=(ELASTICSEARCH_USERNAME, ELASTICSEARCH_PASSWORD) if ELASTICSEARCH_USERNAME else None,
                verify_certs=True # default is True
            )
            ping_msg = self.client.ping()
            logger.info(ping_msg)
            logger.info(self.client.ping())
            if self.client.ping():
                logger.info(f"Successfully connected to ElasticSearch at {ELASTICSEARCH_HOST}")
            else:
                logger.error("ElasticSearch ping failed.")
                self.client = None
        except Exception as e:
            logger.error(f"Error connecting to ElasticSearch: {e!s}")
            self.client = None

    def log_interaction(self, user_id: str, interface: str, query: str, response: str, metadata: dict = None):
        """
        Logs a bot-user interaction for analytics.
        """
        if not self.client:
            return

        doc = {
            "timestamp": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "interface": interface,
            "query": query,
            "response": response,
            "metadata": metadata or {},
            "application_name": AURORA_APPLICATION_NAME,
            "environment": AURORA_APPLICATION_ENVIRONMENT
        }

        try:
            self.client.index(index=self.bot_index, document=doc)
        except Exception as e:
            logger.error(f"Error logging interaction to ES: {e!s}")

    def log_event(self, level: str, message: str, extra: dict = None):
        """
        Logs an application event/log.
        """
        if not self.client:
            return

        doc = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": level,
            "message": message,
            "extra": extra or {},
            "application_name": AURORA_APPLICATION_NAME,
            "environment": AURORA_APPLICATION_ENVIRONMENT
        }

        try:
            self.client.index(index=self.logs_index, document=doc)
        except Exception as e:
            logger.error(f"Error logging event to ES: {e!s}")

    def log_escalation(self, interactions: list, tags: list = None, metadata: dict = None):
        """
        Logs an HR escalation event to a dedicated index.
        """
        if not self.client:
            return

        index_name = f"{AURORA_APPLICATION_NAME}_{AURORA_APPLICATION_ENVIRONMENT}_escalate_data"
        
        doc = {
            "timestamp": datetime.utcnow().isoformat(),
            "interactions": interactions,
            "tags": tags or [],
            "metadata": metadata or {},
            "application_name": AURORA_APPLICATION_NAME,
            "environment": AURORA_APPLICATION_ENVIRONMENT
        }

        try:
            self.client.index(index=index_name, document=doc)
            logger.info(f"Successfully logged escalation to {index_name}")
            return True
        except Exception as e:
            logger.error(f"Error logging escalation to ES: {e!s}")
            return False


# Singleton instance
es_manager = ESManager()
