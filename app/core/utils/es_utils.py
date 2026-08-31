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
        # Define prefixed index names BEFORE _setup_client so _ensure_indices can use them
        self.bot_index = f"{AURORA_APPLICATION_NAME}_{AURORA_APPLICATION_ENVIRONMENT}_{ELASTICSEARCH_BOT_INTERACTION_INDEX}"
        self.logs_index = f"{AURORA_APPLICATION_NAME}_{AURORA_APPLICATION_ENVIRONMENT}_{ELASTICSEARCH_LOGS_INDEX}"
        self._setup_client()

    def _setup_client(self):
        if not ELASTICSEARCH_HOST:
            logger.warning("ELASTICSEARCH_HOST not configured. ES features disabled.")
            return

        verify = False if ELASTICSEARCH_HOST.startswith("http://") else True

        # Attempt 1: Connect without authentication
        try:
            client_no_auth = Elasticsearch([ELASTICSEARCH_HOST], verify_certs=verify)
            if client_no_auth.ping():
                self.client = client_no_auth
                logger.info(f"Successfully connected to ElasticSearch (no auth) at {ELASTICSEARCH_HOST}")
                self._ensure_indices()
                return
        except Exception as e:
            logger.debug(f"ElasticSearch no-auth connection attempt failed: {e}")

        # Attempt 2: Connect with Basic Auth if credentials are configured
        if ELASTICSEARCH_USERNAME:
            try:
                auth = (ELASTICSEARCH_USERNAME, ELASTICSEARCH_PASSWORD)
                client_auth = Elasticsearch([ELASTICSEARCH_HOST], http_auth=auth, verify_certs=verify)
                if client_auth.ping():
                    self.client = client_auth
                    logger.info(f"Successfully connected to ElasticSearch (with basic auth) at {ELASTICSEARCH_HOST}")
                    self._ensure_indices()
                    return
            except Exception as e:
                logger.debug(f"ElasticSearch auth connection attempt failed: {e}")

        logger.error("ElasticSearch ping failed (both no-auth and auth attempts).")
        self.client = None

    def _ensure_indices(self):
        """Pre-create all ESManager-owned indices with explicit mappings if they don't exist yet."""
        # Common keyword+date mapping helper
        def _kw(): return {"type": "keyword"}
        def _txt(): return {"type": "text"}
        def _date(): return {"type": "date"}
        def _obj(): return {"type": "object", "enabled": False}  # freeform JSON blob

        indices_to_create = {
            self.bot_index: {
                "mappings": {
                    "_doc": {
                        "properties": {
                            "timestamp":        _date(),
                            "user_id":          _kw(),
                            "interface":        _kw(),
                            "query":            _txt(),
                            "response":         _txt(),
                            "metadata":         _obj(),
                            "application_name": _kw(),
                            "environment":      _kw(),
                        }
                    }
                }
            },
            self.logs_index: {
                "mappings": {
                    "_doc": {
                        "properties": {
                            "timestamp":        _date(),
                            "level":            _kw(),
                            "message":          _txt(),
                            "extra":            _obj(),
                            "application_name": _kw(),
                            "environment":      _kw(),
                        }
                    }
                }
            },
            f"{AURORA_APPLICATION_NAME}_{AURORA_APPLICATION_ENVIRONMENT}_escalate_data": {
                "mappings": {
                    "_doc": {
                        "properties": {
                            "timestamp":        _date(),
                            "interactions":     {"type": "nested", "dynamic": True},
                            "tags":             _kw(),
                            "metadata":         _obj(),
                            "application_name": _kw(),
                            "environment":      _kw(),
                        }
                    }
                }
            },
        }

        for index_name, body in indices_to_create.items():
            try:
                if not self.client.indices.exists(index=index_name):
                    self.client.indices.create(index=index_name, body=body)
                    logger.info(f"[es_utils] Created index '{index_name}'")
                else:
                    logger.debug(f"[es_utils] Index '{index_name}' already exists")
            except Exception as e:
                logger.warning(f"[es_utils] Could not create index '{index_name}': {e}")

        # Also ensure ticket_store's index is created. Deferred import to avoid circular dependency
        # (es_utils is imported before ticket_store in the module chain).
        try:
            from app.core.graph.ticket_store import ticket_store as _ts
            _ts._ensure_index()
        except Exception as e:
            logger.debug(f"[es_utils] ticket_store._ensure_index deferred call: {e}")

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
