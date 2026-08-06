"""
subgraphs/courses_subgraph.py
------------------------------
Specialist subgraph for course-related SOP tickets on iGOT Karmayogi.

SOP workflows handled (from Agent_SOP_Course_Issues.md):
  SOP-C1: User unable to find / enroll in a course, program, or event
  SOP-C2: Course / Program / Event progress not updating
  SOP-C3: Resource / content not opening
  SOP-C4: Learning progress not reflecting in external portals (eHRMS / Shiksha Path / SPARROW)
  SOP-C5: Request to unenroll from a course / program / event

All tools are sourced from app.core.tools.course_tools.
The full SOP is embedded in COURSES_SYSTEM_PROMPT — no KB lookup required.
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.course_tools import get_course_tools
from app.core.utils.prompt_templates import COURSES_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class CoursesSubgraph(BaseSubgraph):

    CATEGORY = "courses"

    def system_prompt(self, state: TicketState) -> str:
        return COURSES_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "course"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_course_tools()


# Singleton — compiled once at import time
courses_subgraph = CoursesSubgraph().build()
