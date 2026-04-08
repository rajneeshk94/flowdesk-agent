# FlowDesk — Freelance Operations Co-pilot

> A multi-agent AI system that manages tasks, client notes, and scheduling through natural language — built with Google ADK on GCP.

Submitted for the **Gen AI Academy APAC Edition**

[![Cloud Run](https://img.shields.io/badge/Cloud%20Run-Deployed-4285F4?logo=google-cloud&logoColor=white)](https://flowdesk-agent-189656245584.us-central1.run.app)
[![Google ADK](https://img.shields.io/badge/Google%20ADK-1.14.0-34A853?logo=google&logoColor=white)](https://google.github.io/adk-docs/)
[![Gemini](https://img.shields.io/badge/Gemini-2.5%20Flash-EA4335?logo=google&logoColor=white)](https://ai.google.dev/)
[![License](https://img.shields.io/badge/License-MIT-888780)](LICENSE)

---

## What it does

Most freelancers manage tasks in one tool, client notes in another, and meetings in a third. FlowDesk collapses that into a single natural-language interface. You send one message — the system routes it to the right specialist agent, executes real writes against GCP services, and confirms back with exactly what was done.

**Example — one sentence triggers three systems:**

```
"Wrap up the Acme redesign project — mark everything done and schedule a retro"
```

FlowDesk responds by marking all project tasks complete in Firestore, archiving client notes to Cloud Storage, and creating a real event in Google Calendar — without you touching any of them.

---

## Architecture overview

<!-- DIAGRAM: Insert the overview wireframe diagram here -->
<!-- Recommended: Export the overview SVG/PNG and place it at docs/architecture-overview.png -->
<!-- Then replace this comment with: ![Architecture Overview](docs/architecture-overview.png) -->
<img width="1440" height="1270" alt="image" src="https://github.com/user-attachments/assets/4741cf01-ada5-4468-8663-60cbd30e2677" />

---

## Request flow

<!-- DIAGRAM: Insert the step-by-step request flow diagram here -->
<!-- Recommended: Export the flow SVG/PNG and place it at docs/request-flow.png -->
<!-- Then replace this comment with: ![Request Flow](docs/request-flow.png) -->
<img width="1440" height="1610" alt="image" src="https://github.com/user-attachments/assets/5d667f59-421a-47a0-a369-37a83d1f6f5c" />


---

## Agent design

The system is built on **Google ADK** following the `root_agent` + `sub_agents` pattern from the [ADK deployment codelab](https://codelabs.developers.google.com/codelabs/production-ready-ai-with-gc/5-deploying-agents/deploy-an-adk-agent-to-cloud-run).

| Agent | Role | Tools |
|---|---|---|
| `flowdesk_orchestrator` | Root agent — classifies intent, routes to sub-agents, synthesises response | — |
| `project_agent` | Task lifecycle — create, query, update status, find overdue | `create_task`, `get_tasks`, `update_task_status`, `get_overdue_tasks` |
| `client_agent` | Notes and client context — add, retrieve, archive to GCS | `add_note`, `get_notes`, `archive_project_notes` |
| `calendar_agent` | Scheduling — create real Google Calendar events, check upcoming | `create_calendar_event`, `get_upcoming_events` |

All tool functions are plain Python functions registered directly on each agent — no wrapper classes, no manual routing logic. ADK handles state passing between agents via `ToolContext` and `output_key`.

---

## GCP services used

| Service | Purpose |
|---|---|
| **Cloud Run** | Hosts the deployed ADK agent as a serverless HTTPS endpoint |
| **Vertex AI** | Serves Gemini 2.5 Flash for all agent inference |
| **Cloud Firestore** | Stores tasks and client notes (Native mode, no composite indexes needed) |
| **Cloud Storage** | Long-term archive for completed project notes as JSON blobs |
| **Secret Manager** | Stores Google Calendar OAuth token and project credentials |
| **Cloud Build** | Builds and pushes the container image during `adk deploy cloud_run` |
| **Artifact Registry** | Stores the built container image |

---

## Project structure

```
flowdesk/
├── agent.py          # All ADK agents — root orchestrator + 3 sub-agents
├── tools.py          # All MCP tool functions — Firestore, GCS, Calendar
├── __init__.py       # Package entry point (from . import agent)
├── requirements.txt  # Python dependencies
├── .env              # Environment variables (not committed)
└── Dockerfile        # Only needed if building manually; adk deploy handles it
```

---

## Local setup

### Prerequisites

- Python 3.11+
- `uv` package manager — `pip install uv`
- A GCP project with billing enabled
- Google ADK — `pip install google-adk==1.14.0`

### 1. Clone the repo

```bash
git clone https://github.com/your-username/flowdesk-agent.git
cd flowdesk-agent
```

### 2. Create and activate virtual environment

```bash
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
uv pip install -r requirements.txt
```

### 4. Set environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
PROJECT_ID=your-gcp-project-id
PROJECT_NUMBER=your-project-number
SA_NAME=flowdesk-sa
SERVICE_ACCOUNT=flowdesk-sa@your-project-id.iam.gserviceaccount.com
MODEL=gemini-2.5-flash
GCS_BUCKET_NAME=flowdesk-notes-your-project-id
```

### 5. Enable GCP APIs

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  calendar-json.googleapis.com
```

### 6. Set up Google Calendar OAuth

Generate and store a Calendar OAuth token in Secret Manager:

```bash
# Generate token (follow the browser prompt)
python3 scripts/generate_calendar_token.py

# Store in Secret Manager
gcloud secrets create CALENDAR_TOKEN --data-file="calendar_token.json"
```

> The token uses `https://www.googleapis.com/auth/calendar` scope and is automatically refreshed by the tool at runtime.

### 7. Run locally

```bash
source .env
adk web
```

Open `http://localhost:8000` — the ADK developer UI loads and you can chat with FlowDesk immediately.

---

## Deployment

Deploy to Cloud Run with a single ADK CLI command:

```bash
source .env

uvx --from google-adk==1.14.0 \
  adk deploy cloud_run \
  --project=$PROJECT_ID \
  --region=us-central1 \
  --service_name=flowdesk-agent \
  --with_ui \
  . \
  -- \
  --service-account=$SERVICE_ACCOUNT \
  --set-env-vars="PROJECT_ID=$PROJECT_ID,GCS_BUCKET_NAME=$GCS_BUCKET_NAME"
```

When prompted `Allow unauthenticated invocations?` → type `y`.

The command prints your live URL on completion:

```
Service URL: https://flowdesk-agent-xxxxxxxxx-uc.a.run.app
```

---

## API usage

The ADK runtime exposes a `/run` endpoint. All requests follow this shape:

```bash
curl -X POST https://your-cloud-run-url.run.app/run \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "flowdesk",
    "user_id": "demo-user",
    "session_id": "session-1",
    "new_message": {
      "role": "user",
      "parts": [{"text": "YOUR MESSAGE HERE"}]
    }
  }'
```

### Example requests

**Create a task**
```json
{"parts": [{"text": "Create a task: deliver final mockups for TechCorp, project branding, due 2025-07-10, high priority"}]}
```

**Query overdue work**
```json
{"parts": [{"text": "What tasks are overdue for the Acme client?"}]}
```

**Update task status**
```json
{"parts": [{"text": "Mark the TechCorp branding task as complete"}]}
```

**Add a client note**
```json
{"parts": [{"text": "Add a note for Acme: client wants a dark theme variant by end of month"}]}
```

**Schedule a meeting**
```json
{"parts": [{"text": "Schedule a kickoff with Globex on 2025-07-05 at 10:00:00, title Project Kickoff"}]}
```

**Full project wrap-up (multi-agent workflow)**
```json
{"parts": [{"text": "Wrap up the Acme redesign project — mark everything done, archive the notes, and schedule a retro"}]}
```

---

## Key technical decisions

**Why single-field Firestore queries?**
Chaining multiple `.where()` filters in Firestore requires a composite index to be manually created in the GCP Console. To keep setup zero-friction, all queries filter on one field in Firestore and apply additional filters in Python — no index configuration needed.

**Why OAuth token in Secret Manager instead of Application Default Credentials?**
ADC only carries the scopes granted at service account creation time, which does not include Google Calendar. A pre-authorised OAuth refresh token stored in Secret Manager is the correct approach for user-owned Calendar access, and it auto-refreshes at runtime.

**Why ADK `SequentialAgent` for wrap-up?**
The wrap-up workflow intentionally runs all three sub-agents in a fixed order — project first, then client, then calendar — so that each agent's output is available in state for the synthesiser. `SequentialAgent` enforces this without any manual orchestration code.

---

## Live demo

**Cloud Run URL:** `https://flowdesk-agent-189656245584.us-central1.run.app`

Open the URL in your browser to access the ADK web UI and interact with FlowDesk directly.

---

## License

MIT — see [LICENSE](LICENSE) for details.
