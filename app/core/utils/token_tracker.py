"""
app/core/utils/token_tracker.py
--------------------------------
Tracks LLM token consumption per ticket and persists it to Elasticsearch
under the `token_usage_index` index.

Each document records the total prompt / completion / total tokens consumed
across the entire Plan→Execute→Decide loop for a single ticket.

Public helpers
--------------
  token_tracker.record(ticket_id, email, model, prompt_tokens,
                        completion_tokens, total_tokens, node, category)
    → called from base_subgraph after every LLM response

  token_tracker.flush(ticket_id, result)
    → called from kafka_worker after arun_ticket() completes;
      writes the aggregated per-ticket document to ES

  token_tracker.get(ticket_id)
    → fetch the full token usage doc for a ticket

Usage in base_subgraph
-----------------------
  from app.core.utils.token_tracker import token_tracker
  token_tracker.record(tid, email, _llm.model,
                        resp.usage_metadata.get("input_tokens", 0),
                        resp.usage_metadata.get("output_tokens", 0),
                        resp.usage_metadata.get("total_tokens", 0),
                        node="plan_node", category=self.CATEGORY)
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from elasticsearch import Elasticsearch, NotFoundError

from app.core.utils.config import (
    AURORA_APPLICATION_ENVIRONMENT,
    AURORA_APPLICATION_NAME,
    ELASTICSEARCH_HOST,
    ELASTICSEARCH_PASSWORD,
    ELASTICSEARCH_USERNAME,
)

logger = logging.getLogger(__name__)

INDEX_NAME = "token_usage_index"

# ------------------------------------------------------------------
# In-memory accumulator — one entry per live ticket being processed
# Keyed by ticket_id → list of per-call dicts
# ------------------------------------------------------------------
_accumulator: dict[str, list[dict]] = defaultdict(list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TokenTracker:
    """Accumulates per-call token counts and flushes to ES per ticket."""

    def __init__(self):
        self.client: Elasticsearch | None = None
        self._setup()

    # ── Setup ─────────────────────────────────────────────────────

    def _setup(self):
        if not ELASTICSEARCH_HOST:
            logger.warning("[token_tracker] ELASTICSEARCH_HOST not set — token tracking disabled.")
            return
        try:
            self.client = Elasticsearch(
                ELASTICSEARCH_HOST,
                basic_auth=(ELASTICSEARCH_USERNAME, ELASTICSEARCH_PASSWORD),
                verify_certs=True,
                ssl_show_warn=False,
                request_timeout=15,
            )
            if self.client.ping():
                self._ensure_index()
                logger.info(f"[token_tracker] Connected. Using index='{INDEX_NAME}'")
            else:
                logger.error("[token_tracker] ES ping failed.")
                self.client = None
        except Exception as e:
            logger.error(f"[token_tracker] ES setup error: {e}")
            self.client = None

    def _ensure_index(self):
        """Create the index with an explicit mapping if it doesn't exist."""
        try:
            if not self.client.indices.exists(index=INDEX_NAME):
                self.client.indices.create(
                    index=INDEX_NAME,
                    body={
                        "mappings": {
                            "properties": {
                                "ticket_id":        {"type": "keyword"},
                                "email":            {"type": "keyword"},
                                "zoho_ticket_id":   {"type": "keyword"},
                                "category":         {"type": "keyword"},
                                "main_category":    {"type": "keyword"},
                                "model_breakdown":  {"type": "object",   "enabled": True},
                                "node_breakdown":   {"type": "object",   "enabled": True},
                                "total_prompt_tokens":     {"type": "integer"},
                                "total_completion_tokens": {"type": "integer"},
                                "total_tokens":            {"type": "integer"},
                                "llm_calls":        {"type": "integer"},
                                "is_resolved":      {"type": "boolean"},
                                "escalated":        {"type": "boolean"},
                                "retry_count":      {"type": "integer"},
                                "processing_time_s": {"type": "float"},
                                "app_name":         {"type": "keyword"},
                                "environment":      {"type": "keyword"},
                                "created_at":       {"type": "date"},
                                "completed_at":     {"type": "date"},
                            }
                        }
                    },
                )
                logger.info(f"[token_tracker] Created index '{INDEX_NAME}'")
        except Exception as e:
            logger.warning(f"[token_tracker] Could not ensure index: {e}")

    # ── Public: record one LLM call ───────────────────────────────

    def record(
        self,
        ticket_id: str,
        email: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        node: str = "",
        category: str = "",
    ) -> None:
        """
        Accumulate token counts for a single LLM call.
        Safe to call from any subgraph node — data is kept in memory
        until flush() is called by the worker.
        """
        _accumulator[ticket_id].append({
            "email":             email,
            "model":             model,
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens":      total_tokens,
            "node":              node,
            "category":          category,
            "timestamp":         _now(),
        })
        logger.debug(
            f"[token_tracker] recorded ticket={ticket_id} node={node} "
            f"prompt={prompt_tokens} compl={completion_tokens} total={total_tokens}"
        )

    # ── Public: flush after ticket is fully processed ─────────────

    def flush(
        self,
        ticket_id: str,
        result: dict,
        started_at: float,           # time.monotonic() snapshot taken before arun_ticket
    ) -> bool:
        """
        Aggregate accumulated per-call data and write one document to ES.
        Clears the in-memory accumulator for this ticket_id.
        """
        calls = _accumulator.pop(ticket_id, [])
        if not calls:
            logger.warning(f"[token_tracker] No calls recorded for ticket={ticket_id}")
            return False

        if not self.client:
            return False

        # ── Aggregate totals ──────────────────────────────────────
        total_prompt      = sum(c["prompt_tokens"]     for c in calls)
        total_completion  = sum(c["completion_tokens"] for c in calls)
        total_total       = sum(c["total_tokens"]      for c in calls)

        # Per-model breakdown
        model_breakdown: dict[str, dict] = {}
        for c in calls:
            m = c["model"]
            if m not in model_breakdown:
                model_breakdown[m] = {"prompt": 0, "completion": 0, "total": 0, "calls": 0}
            model_breakdown[m]["prompt"]     += c["prompt_tokens"]
            model_breakdown[m]["completion"] += c["completion_tokens"]
            model_breakdown[m]["total"]      += c["total_tokens"]
            model_breakdown[m]["calls"]      += 1

        # Per-node breakdown
        node_breakdown: dict[str, dict] = {}
        for c in calls:
            n = c["node"] or "unknown"
            if n not in node_breakdown:
                node_breakdown[n] = {"prompt": 0, "completion": 0, "total": 0, "calls": 0}
            node_breakdown[n]["prompt"]     += c["prompt_tokens"]
            node_breakdown[n]["completion"] += c["completion_tokens"]
            node_breakdown[n]["total"]      += c["total_tokens"]
            node_breakdown[n]["calls"]      += 1

        elapsed = round(time.monotonic() - started_at, 2)

        doc = {
            "ticket_id":               ticket_id,
            "zoho_ticket_id":          result.get("zoho_ticket_id") or ticket_id,
            "email":                   calls[0]["email"] if calls else "",
            "category":                result.get("category"),
            "main_category":           result.get("main_category"),
            "model_breakdown":         model_breakdown,
            "node_breakdown":          node_breakdown,
            "total_prompt_tokens":     total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens":            total_total,
            "llm_calls":               len(calls),
            "is_resolved":             result.get("is_resolved", False),
            "escalated":               result.get("escalated_to_human", False),
            "retry_count":             result.get("retry_count", 0),
            "processing_time_s":       elapsed,
            "app_name":                AURORA_APPLICATION_NAME,
            "environment":             AURORA_APPLICATION_ENVIRONMENT,
            "created_at":              _now(),
            "completed_at":            _now(),
        }

        import time as _time
        for attempt in range(3):
            try:
                self.client.index(index=INDEX_NAME, id=ticket_id, document=doc)
                logger.info(
                    f"[token_tracker] Flushed ticket={ticket_id} "
                    f"total_tokens={total_total} llm_calls={len(calls)} "
                    f"time={elapsed}s"
                )
                return True
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"[token_tracker] flush failed (attempt {attempt+1}): {e}. Retrying...")
                    _time.sleep(2 ** attempt)
                else:
                    logger.error(f"[token_tracker] flush failed after 3 attempts for {ticket_id}: {e}")
                    return False

    # ── Public: read ──────────────────────────────────────────────

    def get(self, ticket_id: str) -> dict | None:
        """Fetch the token usage document for a ticket."""
        if not self.client:
            return None
        try:
            resp = self.client.get(index=INDEX_NAME, id=ticket_id)
            return resp["_source"]
        except NotFoundError:
            return None
        except Exception as e:
            logger.error(f"[token_tracker] get failed for {ticket_id}: {e}")
            return None

    # ── Public: aggregate stats ───────────────────────────────────

    def aggregate_stats(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict | None:
        """
        Return aggregated token statistics across ALL documents in
        `token_usage_index`.

        Optional filters:
          - date_from: ISO date string — only include tickets created on/after this date
          - date_to:   ISO date string — only include tickets created on/before this date

        Returns:
          {
            "total_tickets":            int,
            "total_prompt_tokens":      int,
            "total_completion_tokens":  int,
            "total_tokens":             int,
            "total_llm_calls":          int,
            "avg_tokens_per_ticket":    float,
            "avg_processing_time_s":    float,
          }
        """
        if not self.client:
            return None

        query: dict = {"match_all": {}}
        if date_from or date_to:
            date_range: dict = {}
            if date_from:
                date_range["gte"] = date_from
            if date_to:
                date_range["lte"] = date_to
            query = {"range": {"created_at": date_range}}

        try:
            resp = self.client.search(
                index=INDEX_NAME,
                body={
                    "size": 0,       # aggregations only, no hits
                    "query": query,
                    "aggs": {
                        "total_prompt":     {"sum": {"field": "total_prompt_tokens"}},
                        "total_completion": {"sum": {"field": "total_completion_tokens"}},
                        "total_tokens":     {"sum": {"field": "total_tokens"}},
                        "total_llm_calls":  {"sum": {"field": "llm_calls"}},
                        "avg_tokens":       {"avg": {"field": "total_tokens"}},
                        "avg_proc_time":    {"avg": {"field": "processing_time_s"}},
                    },
                },
            )
            aggs  = resp["aggregations"]
            count = resp["hits"]["total"]["value"]
            return {
                "total_tickets":           count,
                "total_prompt_tokens":     int(aggs["total_prompt"]["value"]     or 0),
                "total_completion_tokens": int(aggs["total_completion"]["value"] or 0),
                "total_tokens":            int(aggs["total_tokens"]["value"]     or 0),
                "total_llm_calls":         int(aggs["total_llm_calls"]["value"]  or 0),
                "avg_tokens_per_ticket":   round(aggs["avg_tokens"]["value"]    or 0, 2),
                "avg_processing_time_s":   round(aggs["avg_proc_time"]["value"] or 0, 2),
            }
        except Exception as e:
            logger.error(f"[token_tracker] aggregate_stats failed: {e}")
            return None

    # ── Public: per-model stats ────────────────────────────────

    def get_model_stats(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict | None:
        """
        Return per-model token usage aggregated across all tickets.
        Reads the `model_breakdown` field from every doc and merges them.

        Optional filters:
          - date_from: ISO date string — only include tickets created on/after this date
          - date_to:   ISO date string — only include tickets created on/before this date

        Returns:
          {
            "by_model": {
              "gemini-3.5-flash": {"prompt": int, "completion": int, "total": int, "calls": int},
              "gemini-2.5-flash": {...},
              ...
            }
          }
        """
        if not self.client:
            return None

        query: dict = {"match_all": {}}
        if date_from or date_to:
            date_range: dict = {}
            if date_from:
                date_range["gte"] = date_from
            if date_to:
                date_range["lte"] = date_to
            query = {"range": {"created_at": date_range}}

        try:
            merged: dict[str, dict] = {}
            from_ = 0
            page_size = 1000
            while True:
                resp = self.client.search(
                    index=INDEX_NAME,
                    body={
                        "size": page_size,
                        "from": from_,
                        "query": query,
                        "_source": ["model_breakdown"],
                    },
                )
                hits = resp["hits"]["hits"]
                if not hits:
                    break
                for hit in hits:
                    breakdown = hit["_source"].get("model_breakdown") or {}
                    for model_name, counters in breakdown.items():
                        if model_name not in merged:
                            merged[model_name] = {"prompt": 0, "completion": 0, "total": 0, "calls": 0}
                        merged[model_name]["prompt"]     += counters.get("prompt",     0)
                        merged[model_name]["completion"] += counters.get("completion", 0)
                        merged[model_name]["total"]      += counters.get("total",      0)
                        merged[model_name]["calls"]      += counters.get("calls",      0)
                from_ += page_size
                if from_ >= resp["hits"]["total"]["value"]:
                    break
            return {"by_model": merged}
        except Exception as e:
            logger.error(f"[token_tracker] get_model_stats failed: {e}")
            return None

    def get_resolution_time_stats(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict | None:
        """
        Query Elasticsearch to get the top 5 documents sorted by processing_time_s
        both ascending (min time) and descending (max time) to compute averages.

        Optional filters:
          - date_from: ISO date string — only include tickets created on/after this date
          - date_to:   ISO date string — only include tickets created on/before this date
        """
        if not self.client:
            return None

        base_query: dict
        if date_from or date_to:
            date_range: dict = {}
            if date_from:
                date_range["gte"] = date_from
            if date_to:
                date_range["lte"] = date_to
            base_query = {
                "bool": {
                    "must": [
                        {"range": {"processing_time_s": {"gt": 0}}},
                        {"range": {"created_at": date_range}},
                    ]
                }
            }
        else:
            base_query = {"range": {"processing_time_s": {"gt": 0}}}

        try:
            # ── 1. Fetch top 5 ascending (minimum resolution times) ──────────
            query_min = {
                "query": base_query,
                "sort": [{"processing_time_s": {"order": "asc"}}],
                "size": 5,
                "_source": ["ticket_id", "processing_time_s"]
            }
            res_min = self.client.search(index=INDEX_NAME, body=query_min)
            hits_min = res_min["hits"]["hits"]
            min_times = [float(h["_source"]["processing_time_s"]) for h in hits_min if "processing_time_s" in h["_source"]]

            # ── 2. Fetch top 5 descending (maximum resolution times) ─────────
            query_max = {
                "query": base_query,
                "sort": [{"processing_time_s": {"order": "desc"}}],
                "size": 5,
                "_source": ["ticket_id", "processing_time_s"]
            }
            res_max = self.client.search(index=INDEX_NAME, body=query_max)
            hits_max = res_max["hits"]["hits"]
            max_times = [float(h["_source"]["processing_time_s"]) for h in hits_max if "processing_time_s" in h["_source"]]

            avg_min = round(sum(min_times) / len(min_times), 2) if min_times else 0.0
            avg_max = round(sum(max_times) / len(max_times), 2) if max_times else 0.0

            return {
                "average_min_resolution_time_s": avg_min,
                "average_max_resolution_time_s": avg_max,
                "min_resolution_times": min_times,
                "max_resolution_times": max_times
            }
        except Exception as e:
            logger.error(f"[token_tracker] get_resolution_time_stats failed: {e}")
            return None

    def list_all(
        self,
        size: int = 50,
        from_: int = 0,
        email: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        """
        Paginated list of token usage for all tickets, newest first.

        Optional filters:
          - email:     filter by sender email
          - date_from: ISO date string
          - date_to:   ISO date string
        """
        if not self.client:
            return {"total": 0, "token_usages": []}

        must_clauses = []
        if email:
            must_clauses.append({"term": {"email": email}})
        if date_from or date_to:
            date_range: dict = {}
            if date_from:
                date_range["gte"] = date_from
            if date_to:
                date_range["lte"] = date_to
            must_clauses.append({"range": {"created_at": date_range}})

        query = {"bool": {"must": must_clauses}} if must_clauses else {"match_all": {}}

        try:
            resp = self.client.search(
                index=INDEX_NAME,
                body={
                    "query": query,
                    "sort":  [{"created_at": {"order": "desc"}}],
                    "from": from_,
                    "size": size,
                }
            )
            hits = resp["hits"]["hits"]
            token_usages = []
            for h in hits:
                doc = h["_source"]
                doc["_id"] = h["_id"]
                token_usages.append(doc)
            total = resp["hits"]["total"]["value"]
            return {"total": total, "token_usages": token_usages}
        except Exception as e:
            logger.error(f"[token_tracker] list_all failed: {e}")
            return {"total": 0, "token_usages": []}



# ── Singleton ─────────────────────────────────────────────────────
token_tracker = TokenTracker()
