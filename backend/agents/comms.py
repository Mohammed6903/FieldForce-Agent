"""Communications specialist agent.

Drafts the two messages a dispatch produces: a warm, reassuring note to the
customer (written in the customer's own language) and a crisp, factual
dispatch brief for the assigned technician. It has no tools — it is purely a
writing agent operating on the facts the coordinator hands it.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..config import settings

_INSTRUCTION = """\
You are the COMMUNICATIONS specialist on a field-service dispatch crew.

You are given the job facts: customer name, customer language, the diagnosis
(root cause), the chosen technician's name and ETA, and the service type.

Write TWO messages:

1. CUSTOMER message — warm, reassuring, and plainly worded. Confirm help is on
   the way, name the technician, give the ETA, and briefly say what to expect.
   Write it ENTIRELY in the customer's language (the `customer_language` code,
   e.g. "es" = Spanish, "zh" = Chinese, "en" = English). Do not translate it
   to English. Keep it to 2-3 short sentences. No placeholders.

2. TECHNICIAN message — concise and factual, written in English. Include the
   customer name, the likely root cause, the parts to bring, and the ETA. One
   or two sentences, dispatch-brief style.

Return ONLY this exact shape (no preamble, no extra commentary):

CUSTOMER_MESSAGE: <the customer-facing message, in the customer's language>
TECHNICIAN_MESSAGE: <the technician dispatch brief, in English>
"""

comms_agent = LlmAgent(
    name="comms",
    model=settings.gemini_model,
    description=(
        "Drafts a warm customer message in the customer's language and a "
        "concise technician dispatch brief."
    ),
    instruction=_INSTRUCTION,
    tools=[],
)
