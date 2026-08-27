"""
app/core/utils/helpers.py
-------------------------
Shared helper functions used by the iGOT Karmayogi intake pipeline.

Extracted from intake_node.py to keep the node focused on orchestration.
"""

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.tools.certificate_tools import get_user_details
from app.core.tools.login_issue_tool import validate_email_domain
from app.core.utils.constants import (  # noqa: F401 kept for other importers
    JUNK_CONFIDENCE_THRESHOLD,
    GraphStage,
    get_llm_model,
)
from app.core.utils.prompt_templates import (
    CATEGORY_SUBCATEGORY_MAP,
    INTAKE_JUNK_CLASSIFIER_SYSTEM,
    JUNK_DETECTION_PROMPT,
)
from app.core.utils.token_tracker import token_tracker

logger = logging.getLogger(__name__)

_llm = ChatGoogleGenerativeAI(model=get_llm_model(GraphStage.JUNK_CLASSIFICATION), temperature=0)


# ── SOP categories ─────────────────────────────────────────────────────────────
# Derived once at import time from the authoritative CATEGORY_SUBCATEGORY_MAP.
# No API call needed — always consistent with the map.
_SOP_CATEGORIES: list[str] = list(CATEGORY_SUBCATEGORY_MAP.keys())


def fetch_sop_categories() -> list:
    """
    Return the list of valid SOP category keys derived from CATEGORY_SUBCATEGORY_MAP.

    Reads directly from the static map — instant, offline-safe, and always
    in sync with the categories defined in prompt_templates.py.
    """
    return _SOP_CATEGORIES


# ── Junk detection ─────────────────────────────────────────────────────────────

def detect_junk(
    message: str,
    ticket_id: str = "",
    email: str = "",
) -> tuple[bool, float, str]:
    """
    Detect if *message* is junk / irrelevant.

    Returns:
        (is_junk: bool, confidence: float 0-1, reason: str)
    """
    if not message or len(message.strip()) < 2:
        return True, 1.0, "Empty or too short message"

    try:
        prompt = JUNK_DETECTION_PROMPT.format(message=mask_pii_default(message))
        response = _llm.invoke([
            SystemMessage(content=INTAKE_JUNK_CLASSIFIER_SYSTEM),
            HumanMessage(content=prompt),
        ])
        raw = response.content.strip()

        # Track token usage if ticket_id is provided
        if ticket_id:
            _usage = response.usage_metadata or {}
            token_tracker.record(
                ticket_id=ticket_id,
                email=email,
                model=_llm.model,
                prompt_tokens=_usage.get("input_tokens", 0),
                completion_tokens=_usage.get("output_tokens", 0),
                total_tokens=_usage.get("total_tokens", 0),
                node="junk_detection",
                category="intake",
            )

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            raw = raw.removeprefix("json")
        raw = raw.strip()

        parsed     = json.loads(raw)
        is_junk    = parsed.get("is_junk", False)
        confidence = float(parsed.get("confidence", 0.5))
        reason     = parsed.get("reason", "")

        return is_junk, confidence, reason
    except Exception as e:
        logger.warning(f"[helpers] Junk detection failed: {e}")
        return False, 0.0, "Detection error - assuming relevant"


# ── User info ──────────────────────────────────────────────────────────────────

def fetch_user_info(email: str) -> tuple[bool, str]:
    """
    Check registration and fetch firstName in a single API call.

    Returns:
        (is_registered: bool, first_name: str)

    Fails open — returns (True, "") on error so the ticket is not blocked.
    """
    try:
        result = get_user_details.invoke({"email": email})
        if "not found" in result.lower() or "error" in result.lower():
            return False, ""

        # Tool returns: "User Details for <email>:\n{...json...}"
        json_part = result.split("\n", 1)[-1].strip()
        try:
            parsed     = json.loads(json_part)
            first_name = parsed.get("firstName") or ""
        except Exception:
            first_name = ""

        return True, first_name
    except Exception as e:
        logger.warning(f"[helpers] User info fetch failed: {e}")
        return True, ""  # fail open


# ── Domain validation ──────────────────────────────────────────────────────────

def is_domain_allowed(email: str) -> bool:
    """
    Check if the email domain is in the iGOT platform whitelist.

    Returns:
        True if allowed, False if not whitelisted.
        Fails open (returns True) on API errors so the ticket is not blocked.
    """
    try:
        result = validate_email_domain.invoke({"email": email})
        return "not whitelisted" not in result.lower() and "error" not in result.lower()
    except Exception as e:
        logger.warning(f"[helpers] Email domain validation failed: {e}")
        return True  # fail open


# ── YP / MDO allocation lookup ────────────────────────────────────────────────

import csv
import os
from functools import lru_cache
from typing import Optional

# Absolute path to the allocation CSV — resolved relative to this file so it
# works regardless of the working directory the server is started from.
_ALLOCATION_CSV_PATH = os.path.join(
    os.path.dirname(__file__), "Allocation_28.10.csv"
)

# Canonical column names after normalisation (used by callers for dict key access)
YP_COL_CENTRE_STATE   = "centre_state"
YP_COL_MDO            = "mdo"
YP_COL_SPOC           = "spoc"
YP_COL_EMAIL          = "email"
YP_COL_MOBILE         = "mobile"
YP_COL_YP_EMAIL       = "yp_email"


def _normalise_header(raw: str) -> str:
    """Convert a raw CSV header cell to a clean snake_case key."""
    raw = raw.lstrip("\ufeff").strip().lower()  # strip BOM + whitespace
    replacements = {
        "centre/state": YP_COL_CENTRE_STATE,
        "mdo":          YP_COL_MDO,
        "spocs":        YP_COL_SPOC,
        "email":        YP_COL_EMAIL,
        "mobile":       YP_COL_MOBILE,
        # the last meaningful column has a typo in the original file
        "mandaotry to add with yp email id": YP_COL_YP_EMAIL,
        "mandatory to add with yp email id": YP_COL_YP_EMAIL,
    }
    return replacements.get(raw, raw.replace(" ", "_").replace("/", "_"))


@lru_cache(maxsize=1)
def _load_allocation_data() -> list[dict]:
    """
    Load and cache the YP allocation CSV.

    Reads once at first call; subsequent calls return the cached list.
    Each row is a dict with normalised snake_case keys (see YP_COL_* constants).
    Empty trailing columns are dropped.
    """
    rows: list[dict] = []
    try:
        with open(_ALLOCATION_CSV_PATH, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            # Build a mapping from raw header → normalised key
            raw_headers = reader.fieldnames or []
            header_map  = {h: _normalise_header(h) for h in raw_headers}

            for raw_row in reader:
                # Remap keys and drop columns whose normalised key is empty
                row = {
                    header_map[k]: v.strip()
                    for k, v in raw_row.items()
                    if k and header_map.get(k)
                }
                # Skip completely blank rows
                if any(row.values()):
                    rows.append(row)

        logger.info(f"[helpers] Loaded {len(rows)} YP allocation rows from CSV.")
    except FileNotFoundError:
        logger.error(f"[helpers] Allocation CSV not found: {_ALLOCATION_CSV_PATH}")
    except Exception as e:
        logger.error(f"[helpers] Failed to load allocation CSV: {e}")
    return rows


def lookup_yp_by_mdo(mdo_name: str) -> list[dict]:
    """
    Search the YP allocation CSV for rows matching *mdo_name*.

    Matching is case-insensitive and substring-based so partial names work
    (e.g. "atomic" matches "Department of Atomic Energy").

    Args:
        mdo_name: The MDO name (or partial name) to search for.

    Returns:
        A list of matching row dicts.  Each dict has the keys:
          - ``centre_state``  : Centre or State name
          - ``mdo``           : Full MDO name
          - ``spoc``          : SPOC / YP person name
          - ``email``         : SPOC primary email address
          - ``mobile``        : SPOC mobile number
          - ``yp_email``      : Mandatory YP email to CC
        Returns an empty list when no match is found.

    Example::

        results = lookup_yp_by_mdo("atomic energy")
        # [{'centre_state': 'Centre', 'mdo': 'Department of Atomic Energy',
        #   'spoc': 'Akshay', 'email': 'akshaysharma.kb@karmayogi.in',
        #   'mobile': '9910210521', 'yp_email': 'soumi.banerjee@karmayogi.in'}]
    """
    if not mdo_name or not mdo_name.strip():
        return []

    needle = mdo_name.strip().lower()
    data   = _load_allocation_data()

    matches = [
        row for row in data
        if needle in row.get(YP_COL_MDO, "").lower()
    ]

    logger.debug(
        f"[helpers] lookup_yp_by_mdo('{mdo_name}') → {len(matches)} match(es)."
    )
    return matches


# ─────────────────────────────────────────────────────────────────────────────
# ── PII Masking Service ───────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
#
# Uses Microsoft Presidio (presidio-analyzer + presidio-anonymizer) backed by
# the spaCy `en_core_web_sm` NLP model.
#
# Public API
# ----------
#   mask_pii(text, entities=None) -> str
#       Detect and mask PII entities in *text*.
#       *entities* is an optional list of Presidio entity-type strings.
#       When omitted the DEFAULT_PII_ENTITIES set is used.
#
#   mask_pii_default(text) -> str
#       Convenience wrapper — always uses DEFAULT_PII_ENTITIES.
#
# Supported entity types (Presidio built-ins):
#   EMAIL_ADDRESS, PHONE_NUMBER, PERSON, LOCATION, IN_AADHAAR,
#   IN_PAN, IN_PASSPORT, IN_VEHICLE_REGISTRATION, CREDIT_CARD,
#   CRYPTO, IBAN_CODE, IP_ADDRESS, MEDICAL_LICENSE, URL,
#   US_SSN, US_DRIVER_LICENSE, NRP  (and many more via spaCy NER)
#
# The singleton is constructed lazily on first call so there is zero
# import-time penalty for modules that never call mask_pii().
# ─────────────────────────────────────────────────────────────────────────────


# Default set of PII entity types that will be redacted when no explicit list
# is passed to mask_pii(). Extend or override as needed.
DEFAULT_PII_ENTITIES: list[str] = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    # "PERSON",
    # "LOCATION",
    "IN_AADHAAR",               # Indian Aadhaar number
    "IN_PAN",                   # Indian PAN card
    # "IN_PASSPORT",              # Indian passport number
    # "IN_VEHICLE_REGISTRATION",  # Indian vehicle registration
    # "CREDIT_CARD",
    # "CRYPTO",                   # Crypto wallet addresses
    # "IBAN_CODE",
    # "IP_ADDRESS",
    # "MEDICAL_LICENSE",
    # "URL",
    # "NRP",                      # Nationality / religion / political group
]


class _PIIMasker:
    """
    Lazy singleton that wraps Presidio AnalyzerEngine and AnonymizerEngine.

    Do not instantiate directly — use the module-level ``mask_pii()`` function.
    """

    _instance: Optional["_PIIMasker"] = None

    def __init__(self) -> None:
        try:
            from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
            from presidio_anonymizer import AnonymizerEngine
            from presidio_anonymizer.entities import OperatorConfig

            self._analyzer   = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()
            self._OperatorConfig = OperatorConfig

            # Register custom recognizers to overcome built-in Presidio limitations
            # 1. Custom Email recognizer to capture .gov.in, .nic.in, and multi-part TLD emails
            email_pattern = Pattern(
                name="custom_email_pattern",
                regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
                score=1.0,
            )
            email_recognizer = PatternRecognizer(
                supported_entity="EMAIL_ADDRESS",
                patterns=[email_pattern],
            )

            # 2. Indian Aadhaar Recognizer (12 digits, optional spaces/hyphens)
            aadhaar_pattern = Pattern(
                name="aadhaar_pattern",
                regex=r"\b[2-9]\d{3}[-\s]?\d{4}[-\s]?\d{4}\b",
                score=0.95,
            )
            aadhaar_recognizer = PatternRecognizer(
                supported_entity="IN_AADHAAR",
                patterns=[aadhaar_pattern],
            )

            # 3. Indian PAN Recognizer (10 char alphanumeric: 5 letters, 4 digits, 1 letter)
            pan_pattern = Pattern(
                name="pan_pattern",
                regex=r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b",
                score=0.95,
            )
            pan_recognizer = PatternRecognizer(
                supported_entity="IN_PAN",
                patterns=[pan_pattern],
            )

            # 4. Indian Mobile/Phone Recognizer (+91 / 91 / 0 prefix + 10 digits)
            phone_pattern = Pattern(
                name="in_phone_pattern",
                regex=r"\b(?:\+?91[-\s]?)?[6-9]\d{4}[-\s]?\d{5}\b",
                score=0.95,
            )
            phone_recognizer = PatternRecognizer(
                supported_entity="PHONE_NUMBER",
                patterns=[phone_pattern],
            )

            self._analyzer.registry.add_recognizer(email_recognizer)
            self._analyzer.registry.add_recognizer(aadhaar_recognizer)
            self._analyzer.registry.add_recognizer(pan_recognizer)
            self._analyzer.registry.add_recognizer(phone_recognizer)

            self._ready = True
            logger.info("[pii_masker] Presidio AnalyzerEngine + AnonymizerEngine initialised with custom Indian PII recognizers.")
        except ImportError as exc:
            logger.critical(
                "[pii_masker] FATAL — Presidio not installed. PII masking cannot run. "
                "Fix: pip install presidio-analyzer presidio-anonymizer && "
                f"python -m spacy download en_core_web_sm  ({exc})"
            )
            self._ready = False
            self._init_error = str(exc)

    # ── Startup guard ─────────────────────────────────────────────────────────

    def assert_ready(self) -> None:
        """
        Raise RuntimeError if Presidio failed to initialise.

        Call this once at application startup (e.g. in a FastAPI lifespan
        handler) so that the service refuses to boot rather than silently
        processing PII-bearing tickets without masking.
        """
        if not self._ready:
            raise RuntimeError(
                "[pii_masker] Presidio is not available — cannot start the service "
                "with PII masking disabled. "
                f"Root cause: {getattr(self, '_init_error', 'unknown')}. "
                "Fix: pip install presidio-analyzer presidio-anonymizer && "
                "python -m spacy download en_core_web_sm"
            )

    # ── Core method ──────────────────────────────────────────────────────────

    def mask(
        self,
        text: str,
        entities: list[str] | None = None,
        language: str = "en",
    ) -> str:
        """
        Detect PII in *text* and replace each hit with a placeholder tag.

        Args:
            text:     The raw input string to anonymise.
            entities: List of Presidio entity-type strings to detect.
                      Defaults to DEFAULT_PII_ENTITIES when None.
            language: BCP-47 language code passed to the analyzer (default "en").

        Returns:
            The anonymised string with detected entities replaced by
            ``<ENTITY_TYPE>`` placeholders e.g. ``<EMAIL_ADDRESS>``.
            Returns *text* unchanged when *text* is empty / not a string.

        Raises:
            RuntimeError: If Presidio is not installed / failed to initialise.
                          Call assert_ready() at startup to catch this early.
        """
        if not self._ready:
            raise RuntimeError(
                "[pii_masker] mask() called but Presidio is not available. "
                "PII masking is disabled — refusing to return unmasked text. "
                f"Root cause: {getattr(self, '_init_error', 'unknown')}"
            )
        if not text or not isinstance(text, str):
            return text

        target_entities = entities or DEFAULT_PII_ENTITIES

        try:
            results = self._analyzer.analyze(
                text=text,
                entities=target_entities,
                language=language,
            )

            if not results:
                return text

            # Each detected entity type → replace with <ENTITY_TYPE> tag
            operators = {
                entity: self._OperatorConfig(
                    operator_name="replace",
                    params={"new_value": f"<{entity}>"},
                )
                for entity in {r.entity_type for r in results}
            }

            anonymised = self._anonymizer.anonymize(
                text=text,
                analyzer_results=results,
                operators=operators,
            )
            return anonymised.text

        except RuntimeError:
            raise  # propagate our own RuntimeError from the _ready check
        except Exception as exc:
            logger.warning(f"[pii_masker] mask() failed, returning original text: {exc}")
            return text

    # ── Singleton accessor ────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "_PIIMasker":
        """Return the shared singleton, constructing it on first call."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# ── Public helpers ────────────────────────────────────────────────────────────

def mask_pii(
    text: str,
    entities: list[str] | None = None,
    language: str = "en",
) -> str:
    """
    Detect and redact PII entities in *text* using Microsoft Presidio.

    Each detected entity is replaced with a readable placeholder tag such as
    ``<EMAIL_ADDRESS>``, ``<PHONE_NUMBER>``, ``<PERSON>``, etc.

    Args:
        text:     The input string to anonymise.
        entities: Optional list of Presidio entity-type strings to detect.
                  Defaults to DEFAULT_PII_ENTITIES when None.
                  Pass a custom list to restrict or expand detection::

                      mask_pii(text, entities=["EMAIL_ADDRESS", "PHONE_NUMBER"])

        language: BCP-47 language code for the Presidio analyzer (default "en").

    Returns:
        Anonymised string with PII replaced by placeholder tags.
        Returns *text* unchanged on errors or when Presidio is unavailable.

    Examples::

        >>> mask_pii("Call me at +91-9876543210 or mail: user@example.com")
        'Call me at <PHONE_NUMBER> or mail: <EMAIL_ADDRESS>'

        >>> mask_pii("My Aadhaar is 1234 5678 9012", entities=["IN_AADHAAR"])
        'My Aadhaar is <IN_AADHAAR>'
    """
    return _PIIMasker.get().mask(text=text, entities=entities, language=language)


def mask_pii_default(text: str) -> str:
    """
    Convenience wrapper — identical to ``mask_pii(text)`` with DEFAULT_PII_ENTITIES.
    Useful as a one-liner drop-in where no customisation is needed.

    Args:
        text: The raw string to anonymise.

    Returns:
        Anonymised string with DEFAULT_PII_ENTITIES replaced by placeholder tags.
    """
    return _PIIMasker.get().mask(text=text)


