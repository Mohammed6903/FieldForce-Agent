"""Operations Analyst — live situational awareness via the Elastic MCP server.

This is the crew's MCP-powered specialist, and the hackathon's required partner
integration. Unlike the other specialists (which call typed native Python
tools), the analyst talks to Elasticsearch *through Elastic's official MCP
server* — composing live queries at runtime with the MCP `search` /
`list_indices` / `get_mappings` tools.

It answers free-form operational questions (current open-job load by severity,
technician availability, competing high-priority work) so the Coordinator can
weigh urgency before committing a dispatch. MCP is therefore a genuine,
load-bearing organ of the crew's reasoning — not a decoration.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..config import settings
from ..mcp_setup import get_elastic_mcp_toolset

# Built once and reused across dispatches. Connection to the MCP server is lazy
# (established on first use, on the running event loop), so importing is cheap.
_elastic_mcp = get_elastic_mcp_toolset(
    tool_filter=["search", "list_indices", "get_mappings"]
)

_INSTRUCTION = """\
You are the OPERATIONS ANALYST for a field-service business. You answer
operational questions by querying the LIVE Elasticsearch cluster THROUGH the
Elastic MCP server tools available to you: `search`, `list_indices`,
`get_mappings`. You must ALWAYS obtain numbers by actually calling these tools —
never guess or fabricate counts.

Relevant indices and fields:
  - `jobs`: status (new|assigned|scheduled|in_progress|done|cancelled),
    severity (low|medium|high|critical), service_type, sla_deadline (date).
  - `technicians`: status (available|on_job|off_shift), skills, service_types.

When the coordinator asks for the operational picture around an incoming job:
  1. Use `search` on the `jobs` index with size 0 and a terms aggregation on
     `severity`, filtered to open jobs (status not done/cancelled), to count
     open jobs by severity.
  2. Use `search` on the `technicians` index (size 0, terms agg on `status`, or
     a filtered count) to gauge how many technicians are currently available.
Then return a SHORT factual briefing (2-3 sentences): the open-job load by
severity, how many technicians are available, and whether the incoming job
faces contention for resources. Keep result payloads small (size 0 + aggs).
"""

operations_analyst_agent = LlmAgent(
    name="operations_analyst",
    model=settings.gemini_model,
    description=(
        "Live operations analyst. Queries Elasticsearch through the Elastic MCP "
        "server (search / list_indices / get_mappings) to brief the coordinator "
        "on current job load, SLA pressure, and technician availability."
    ),
    instruction=_INSTRUCTION,
    tools=[_elastic_mcp],
)
