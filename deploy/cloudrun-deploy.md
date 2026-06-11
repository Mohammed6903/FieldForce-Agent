# Deploying FieldForce to Cloud Run

FieldForce ships as a single container (FastAPI serving both the API and the frontend, plus Node.js
for Elastic's MCP server). These steps build it and deploy it to **Cloud Run**.

## Prerequisites

- `gcloud` CLI authenticated: `gcloud auth login` and `gcloud config set project <PROJECT_ID>`
- Required APIs enabled:
  ```bash
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
      artifactregistry.googleapis.com aiplatform.googleapis.com
  ```
- An Elastic Cloud deployment (endpoint + API key) and a GCP project with Vertex AI access.

## 1. Build the image

Build with Cloud Build (uses `deploy/Dockerfile`):

```bash
export PROJECT_ID=$(gcloud config get-value project)
export REGION=us-central1
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/fieldforce/fieldforce:latest"

# One-time: create the Artifact Registry repo
gcloud artifacts repositories create fieldforce \
    --repository-format=docker --location="$REGION" || true

gcloud builds submit --tag "$IMAGE" --file deploy/Dockerfile .
```

## 2. Deploy to Cloud Run

Pass the runtime configuration as environment variables (mirrors `.env`). Cloud Run injects `$PORT`,
which the container honors automatically.

```bash
gcloud run deploy fieldforce \
    --image "$IMAGE" \
    --region "$REGION" \
    --allow-unauthenticated \
    --port 8080 \
    --cpu 2 --memory 2Gi \
    --timeout 300 \
    --set-env-vars "\
ELASTIC_ENDPOINT=https://your-deployment.es.cloud:443,\
SEMANTIC_INFERENCE_ID=.elser-2-elasticsearch,\
GOOGLE_GENAI_USE_VERTEXAI=TRUE,\
GOOGLE_CLOUD_PROJECT=$PROJECT_ID,\
GOOGLE_CLOUD_LOCATION=global,\
GEMINI_MODEL=gemini-3.1-pro-preview,\
DISPATCH_CITY=San Francisco,\
DISPATCH_CITY_LAT=37.7749,\
DISPATCH_CITY_LON=-122.4194"
```

### Secrets

Keep the Elastic API key out of `--set-env-vars`. Store it in Secret Manager and mount it:

```bash
echo -n "<base64-elastic-api-key>" | \
    gcloud secrets create elastic-api-key --data-file=-

gcloud run services update fieldforce --region "$REGION" \
    --set-secrets "ELASTIC_API_KEY=elastic-api-key:latest"
```

> If you use a Google AI Studio key instead of Vertex AI, set
> `GOOGLE_GENAI_USE_VERTEXAI=FALSE` and add `GOOGLE_API_KEY` as a secret the same way.

### Service account / Vertex AI

With `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, the Cloud Run service account needs Vertex AI access:

```bash
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
    --role="roles/aiplatform.user"
```

## 3. One-time data setup

The indices and demo data live in Elastic, not in the container, so seed them once from any machine
with the same `.env` (or run the equivalent as a Cloud Run Job):

```bash
uv run python -m backend.indices
uv run python -m data.generate --recreate
```

## 4. Verify

```bash
gcloud run services describe fieldforce --region "$REGION" --format='value(status.url)'
```

Open the printed URL — the dispatcher frontend is served from `/`.

---

## Note: ADK → Vertex AI Agent Engine

Cloud Run hosts the full app (UI + API + agents) as a container — the simplest path for the demo,
and it keeps the FastAPI-served frontend and the MCP/Node runtime together in one place.

For a managed agent runtime, the same ADK agent crew can instead be deployed to **Vertex AI Agent
Engine** (part of Google Cloud Agent Builder). The ADK agents in `backend/agents/` are framework
objects, so the path is: package the root (Coordinator) agent, then deploy it with the ADK / Agent
Engine tooling (`vertexai` SDK / `adk deploy agent_engine`), which provisions a managed,
autoscaled endpoint and handles sessions and tracing. The FastAPI layer in this repo then becomes a
thin client that calls that managed endpoint rather than running the agents in-process. The Elastic
MCP server can be co-located with the agent runtime the same way it is in the container here.
