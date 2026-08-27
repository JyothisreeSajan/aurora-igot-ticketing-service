"""
subgraphs/certificate_subgraph.py
-----------------------------------
Specialist subgraph for certificate-related SOP tickets on iGOT Karmayogi.

SOP workflows handled (from Agent_SOP_Certificate_Issues.md):
  SOP-01: Program progress not updating / Program certificate not generated
  SOP-02: Course certificate not generated / Unable to download certificate
  SOP-03: Incorrect name on certificate

All tools are sourced from app.core.tools.certificate_tools.
The full SOP is embedded in CERTIFICATE_SYSTEM_PROMPT — no KB lookup required.
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.certificate_tools import get_certificate_tools
from app.core.utils.prompt_templates import CERTIFICATE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class CertificateSubgraph(BaseSubgraph):

    CATEGORY = "certificate"

    def system_prompt(self, state: TicketState) -> str:
        return CERTIFICATE_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "certificate"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_certificate_tools()


# Singleton — compiled once at import time
certificate_subgraph = CertificateSubgraph().build()
