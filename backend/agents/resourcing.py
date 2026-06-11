"""Resourcing specialist agent.

Given a job's location plus the skills and parts the diagnostics playbook
calls for, this agent finds the single best technician to dispatch (nearest
available with the right skills, including a drive-time ETA) and confirms
whether the required parts are in stock nearby.

It is read-only — it surfaces options but never assigns or reserves anything.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..config import settings
from ..tools.search_tools import check_parts_availability, find_candidate_technicians

_INSTRUCTION = """\
You are the RESOURCING specialist on a field-service dispatch crew.

You are given a job's location (latitude/longitude), its service type, the
required skills, and the required parts (from the diagnostics playbook).

Process:
1. Call `find_candidate_technicians` with the latitude, longitude, the
   required skills, and the service type to rank nearby available technicians.
2. Call `check_parts_availability` with the required part names and the same
   latitude/longitude to confirm stock. Skip this step if no parts are needed.
3. Choose the SINGLE best technician — generally the nearest available one who
   has the skills; break ties by higher rating and shorter ETA.

Return ONLY this exact shape (plain text, no preamble):

TECHNICIAN_ID: <id of the chosen technician>
TECHNICIAN_NAME: <name>
ETA_MINUTES: <integer eta of the chosen technician>
TECH_LANGUAGES: <comma-separated language codes the technician speaks>
PARTS: <comma-separated "name:id:in_stock" triples, or "none">
PARTS_ALL_AVAILABLE: <true|false>
NOTES: <one short sentence on availability / any gap, e.g. a missing part>

If NO suitable technician is found, set TECHNICIAN_ID to "none" and explain in
NOTES. Be decisive — the coordinator will act on your single recommendation.
"""

resourcing_agent = LlmAgent(
    name="resourcing",
    model=settings.gemini_model,
    description=(
        "Finds the best technician to dispatch (nearest available with the "
        "right skills, plus ETA) and checks whether required parts are in "
        "stock nearby."
    ),
    instruction=_INSTRUCTION,
    tools=[find_candidate_technicians, check_parts_availability],
)
