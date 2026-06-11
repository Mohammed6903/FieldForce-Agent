# FieldForce

**An autonomous multi-agent field-service dispatcher** — describe a broken HVAC unit, leaking
pipe, or downed access point in plain language and a crew of AI agents diagnoses the fault, finds
the closest qualified technician, checks parts on hand, drafts the customer message, and produces a
ready-to-approve dispatch plan.

> Built with the **Agent Development Kit (ADK)** and deployed on **Vertex AI Agent Engine —
> Google Cloud Agent Builder** — using **Gemini 3**, integrating **Elastic's MCP server**.

---

## What it does

A new service request arrives (`new` job). The crew turns it into a `DispatchPlan`:

1. **Assess** the live operational picture — open jobs by severity and technician availability —
   queried straight from Elasticsearch **through Elastic's MCP server**.
2. **Diagnose** the likely root cause from the description by recalling similar past incidents.
3. **Resource** the job — rank nearby technicians by travel distance and skill match, and confirm
   the required parts are in stock and close by.
4. **Communicate** — draft the customer-facing notification in the customer's language.
5. **Propose** a set of concrete write actions (assign, reserve parts, notify, schedule) that a
   human dispatcher reviews and approves before anything is committed.

Nothing is written to the system of record until a human clicks approve — the agents *plan*, the
dispatcher *commits*.

---

## Architecture

FieldForce is an **orchestrator-worker** crew: a **Coordinator** agent decomposes the job and
delegates to four specialists, each owning a slice of the problem and a small, focused tool
surface. One specialist — the **Operations Analyst** — reads the live cluster *exclusively through
Elastic's MCP server*; the others use focused native Elasticsearch tools. The specialists never
talk to each other directly — they read and write a shared **blackboard** (the in-progress
`DispatchPlan`) that the Coordinator assembles.

```
                            ┌──────────────────────────────┐
          new job  ───────► │        COORDINATOR           │
                            │  (orchestrator / planner)    │
                            │  • decomposes the request     │
                            │  • dynamic delegation         │
                            │  • assembles the DispatchPlan │
                            └───────────────┬──────────────┘
                                            │ delegates
        ┌──────────────────┬────────────────┼────────────────┬──────────────────┐
        ▼                  ▼                ▼                ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│  OPERATIONS   │ │  DIAGNOSTICS  │ │   RESOURCING  │ │     COMMS     │
│   ANALYST     │ │ root-cause via│ │ rank techs by │ │ draft customer│
│ live load via │ │ similar-incid.│ │ geo + skills; │ │ message in the│
│  ELASTIC MCP  │ │ recall (ELSER)│ │ check parts   │ │ right language│
└──────┬────────┘ └──────┬────────┘ └──────┬────────┘ └──────┬────────┘
       │ MCP search      │ native ES        │ native ES       │
       ▼                 ▼                  ▼                 ▼
┌────────────────────┐  ┌──────────────────────────────────────────────┐
│  Elastic MCP server │  │  Shared blackboard (DispatchPlan in state)    │
│ @elastic/mcp-server │  └───────────────────────┬──────────────────────┘
└─────────┬───────────┘                          │ proposed_actions
          └──────────────► Elasticsearch          ▼
                                            ┌─────────────────────────────┐
                                            │     HUMAN APPROVAL GATE      │
                                            │   dispatcher reviews & OKs    │
                                            └──────────────┬──────────────┘
                                                           │ approved
                                                           ▼
                                            ┌─────────────────────────────┐
                                            │  WRITE actions execute        │
                                            │  assign / reserve / notify /  │
                                            │  reschedule  → Elasticsearch  │
                                            └─────────────────────────────┘
```

### Agentic patterns in play

- **Orchestrator-worker** — the Coordinator owns the goal and the plan; specialists own narrow
  capabilities.
- **Dynamic delegation** — the Coordinator decides at runtime which specialists to call and in what
  order based on the job (e.g. a parts-heavy job leans on Resourcing; a vague complaint leans on
  Diagnostics first).
- **Retrieval-augmented reasoning** — Diagnostics grounds its root-cause guess in real past
  incidents recalled from Elastic via semantic search, not the model's priors alone.
- **Live tool-use through a partner MCP server** — the Operations Analyst has no native tools; it
  reasons by issuing live `search` calls to Elasticsearch through Elastic's MCP server at runtime,
  so its situational briefing reflects the real current state of the cluster.
- **Plan-then-execute with a HUMAN APPROVAL GATE** — agents only emit `proposed_actions`; the
  write tools (`assign_job`, `reserve_parts`, `notify`, `reschedule_job`) run **only after** a human
  approves. The plan and the execution are deliberately separated.
- **Shared-state blackboard** — agents collaborate by reading/writing the evolving `DispatchPlan`
  in shared session state rather than passing long messages around.

---

## Why Elastic is the superpower

The crew's "senses" are Elasticsearch queries that a plain LLM simply cannot do on its own:

- **`geo_distance` technician ranking** — find the closest *available* technician to the job site,
  sorted by real travel distance and turned into an ETA — not a hallucinated guess.
- **Multi-constraint filtering** — combine geo radius with `required_skills`, `service_type`,
  certifications, and `status` in one query to shortlist only technicians who can actually do the
  work.
- **`semantic_text` / ELSER similar-incident recall** — retrieve the most semantically similar past
  incidents to the free-text complaint (with a lexical fallback), giving Diagnostics grounded
  root-cause and parts/skills priors.
- **Aggregations for SLA risk** — a single aggregation buckets at-risk jobs by severity so the
  Coordinator can triage the queue, not just the one job in front of it.

Elastic's official **MCP server** (`@elastic/mcp-server-elasticsearch`) is attached to the
Operations Analyst agent via ADK's `McpToolset`. That agent does its entire job — assessing live
job load and technician availability — by issuing `search` / `list_indices` / `get_mappings` calls
to Elasticsearch **through MCP** at runtime. The MCP integration is therefore *load-bearing* (a
specialist literally cannot function without it), not a decorative add-on, and it complements the
typed native tools the other specialists use in `backend/tools/`.

---

## Tech stack

| Layer            | Choice                                                            |
|------------------|-------------------------------------------------------------------|
| Agents           | Google **ADK** (`google-adk`) orchestrator + 4 specialist agents  |
| Model            | **Gemini 3** (`gemini-3.1-pro-preview`) via Vertex AI            |
| Search / memory  | **Elasticsearch** (geo, multi-constraint, ELSER `semantic_text`)  |
| Partner MCP      | **Elastic MCP server** (`@elastic/mcp-server-elasticsearch`)      |
| API / serving    | **FastAPI** + Uvicorn (also serves the frontend)                  |
| Data models      | **Pydantic** v2                                                   |
| Packaging        | **uv** (Python 3.12)                                              |
| Deploy           | Docker → **Cloud Run** / Vertex AI Agent Engine                  |

### Repo layout

```
fieldforce-agent/
├── backend/
│   ├── config.py            # settings + index names (single source of truth)
│   ├── elastic_client.py    # cached Elasticsearch client + ping
│   ├── indices.py           # creates the 5 indices (run as a module)
│   ├── models.py            # Pydantic models (Job, Technician, DispatchPlan, …)
│   ├── mcp_setup.py         # attaches Elastic's MCP server via ADK McpToolset
│   ├── agents/              # Coordinator + Analyst(MCP) / Diagnostics / Resourcing / Comms
│   ├── tools/
│   │   ├── search_tools.py  # READ tools — the crew's senses
│   │   └── action_tools.py  # WRITE tools — the crew's hands (run post-approval)
│   └── main.py              # FastAPI app (serves API + frontend)
├── data/
│   └── generate.py          # synthetic technicians / jobs / parts / incidents
├── frontend/                # dispatcher UI (served by FastAPI)
├── deploy/
│   ├── Dockerfile
│   └── cloudrun-deploy.md
├── pyproject.toml           # managed by uv
└── LICENSE
```

---

## Setup

FieldForce uses [**uv**](https://docs.astral.sh/uv/) for Python dependency management.

```bash
# 1. Install dependencies
uv sync

# 2. Configure credentials
cp .env.example .env
#    Then fill in:
#      ELASTIC_ENDPOINT, ELASTIC_API_KEY        (from your Elastic Cloud deployment)
#      GOOGLE_CLOUD_PROJECT                      (your GCP project; Vertex AI enabled)
#    Defaults for SEMANTIC_INFERENCE_ID, GEMINI_MODEL, and the demo city are sensible.

# 3. Create the Elasticsearch indices (technicians, jobs, parts, incidents, actions_log)
uv run python -m backend.indices

# 4. Seed the demo data (synthetic fleet, queue, inventory, incident history)
uv run python -m data.generate --recreate

# 5. Run the app
uv run uvicorn backend.main:app --port 8080
```

Then open **http://localhost:8080**.

> **Node.js / npx required.** The Elastic MCP server runs as
> `npx -y @elastic/mcp-server-elasticsearch`, so Node.js must be installed on the host (or in the
> container — the Dockerfile installs it). The Operations Analyst depends on it; if it is missing,
> the crew degrades gracefully (the dispatch still completes without the live situational briefing).
>
> **IPv4 note.** On DNS64/NAT64 networks Node may resolve Elastic Cloud to an unreachable IPv6
> address. `mcp_setup.py` passes `NODE_OPTIONS=--dns-result-order=ipv4first
> --no-network-family-autoselection` to the MCP server to force the reachable IPv4 path.

---

## Status & demo prep

Everything is wired and verified end to end — data seeding, all four agents, the Elastic MCP
integration, the human-approval gate, and persisted write actions all work against a live Elastic
Cloud cluster and Gemini 3 on Vertex AI. Before recording the demo, optionally **tune the synthetic
data** in `data/generate.py` so the scenario (skills, parts, incident history) matches your
walkthrough, and re-seed with `uv run python -m data.generate --recreate`.

---

## Deployment

See [`deploy/cloudrun-deploy.md`](deploy/cloudrun-deploy.md) for building the container and shipping
it to Cloud Run, plus a note on the ADK → Vertex AI Agent Engine path.

---

## License

MIT — see [`LICENSE`](LICENSE).
