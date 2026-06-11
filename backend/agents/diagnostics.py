"""Diagnostics specialist agent.

Given a free-text problem description, this agent recalls the most similar
resolved jobs (via Elasticsearch semantic search) and distils them into a
"playbook": the likely root cause, the skills a technician will need, the
parts likely required, and an estimated on-site duration.

It is a pure reasoning + retrieval agent — it never writes state.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..config import settings
from ..tools.search_tools import find_similar_incidents

_INSTRUCTION = """\
You are the DIAGNOSTICS specialist on a field-service dispatch crew.

You are given a new service problem (a description, and possibly a service
type and severity). Your job is to produce a concise, actionable PLAYBOOK.

Process:
1. Call `find_similar_incidents` with the problem description to recall the
   most similar resolved jobs from history.
2. Reason over those incidents (their root causes, the parts and skills they
   used, and how long they took) to infer what THIS job most likely needs.
   Prefer evidence from the similar incidents over guessing.

Return ONLY a tight playbook in this exact shape (plain text, no preamble):

ROOT_CAUSE: <one sentence, the single most likely cause>
REQUIRED_SKILLS: <comma-separated skill keywords>
REQUIRED_PARTS: <comma-separated part names, or "none">
EST_DURATION_MINUTES: <integer best estimate>
RATIONALE: <one short sentence citing the similar incidents you relied on>

Be specific and decisive — the coordinator will act on this directly.
"""

diagnostics_agent = LlmAgent(
    name="diagnostics",
    model=settings.gemini_model,
    description=(
        "Diagnoses a field-service problem by recalling similar past jobs and "
        "returns a playbook: root cause, required skills, required parts, and "
        "estimated duration."
    ),
    instruction=_INSTRUCTION,
    tools=[find_similar_incidents],
)
