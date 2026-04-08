import os
import json
import logging
from datetime import datetime, timezone, timedelta
from google.cloud import firestore, storage, secretmanager
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.adk.tools.tool_context import ToolContext

db = firestore.Client()

# ─── Secret helper ────────────────────────────────────────────

def _get_secret(secret_id: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    project_id = os.environ.get("PROJECT_ID", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

def _bucket_name() -> str:
    return os.environ.get("GCS_BUCKET_NAME", "")

# ─── Calendar helper ───────────────────────────────────────────

def _calendar_service():
    """Build Calendar service from stored OAuth token in Secret Manager."""
    token_json = _get_secret("CALENDAR_TOKEN")
    token_data = json.loads(token_json)
    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"],
    )
    # Auto-refresh if expired
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

# ─── Task tools ────────────────────────────────────────────────

def create_task(
    tool_context: ToolContext,
    client_name: str,
    project_name: str,
    title: str,
    due_date: str,
    priority: str = "medium",
) -> dict:
    """
    Creates a new task in Firestore for a given client and project.
    due_date must be in YYYY-MM-DD format.
    priority must be one of: low, medium, high.
    Returns the new task ID.
    """
    doc_ref = db.collection("tasks").document()
    data = {
        "client_name": client_name.strip().lower(),
        "project_name": project_name.strip().lower(),
        "title": title.strip(),
        "due_date": due_date,
        "priority": priority,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    doc_ref.set(data)
    logging.info(f"[create_task] Created {doc_ref.id}: {title}")
    tool_context.state["last_created_task"] = doc_ref.id
    return {"success": True, "task_id": doc_ref.id, "title": title}


def get_tasks(
    tool_context: ToolContext,
    client_name: str = "",
    status: str = "",
) -> dict:
    """
    Retrieves tasks from Firestore.
    Optionally filter by client_name and/or status (pending, complete, in_progress).
    Leave blank to retrieve all tasks.
    """
    # Query on ONE field only to avoid needing composite indexes
    query = db.collection("tasks")
    if client_name:
        query = query.where("client_name", "==", client_name.strip().lower())
    
    docs = list(query.limit(50).stream())
    tasks = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        # Filter status in Python — avoids composite index requirement
        if status and d.get("status", "") != status:
            continue
        tasks.append(d)

    logging.info(f"[get_tasks] Retrieved {len(tasks)} tasks")
    return {"success": True, "tasks": tasks, "count": len(tasks)}


def update_task_status(
    tool_context: ToolContext,
    task_id: str,
    new_status: str,
) -> dict:
    """
    Updates the status of a task by its exact task_id.
    new_status must be one of: pending, in_progress, complete.
    Use get_tasks first to find the task_id.
    """
    valid_statuses = {"pending", "in_progress", "complete"}
    if new_status not in valid_statuses:
        return {"success": False, "error": f"Status must be one of {valid_statuses}"}
    
    doc_ref = db.collection("tasks").document(task_id)
    doc = doc_ref.get()
    if not doc.exists:
        return {"success": False, "error": f"Task {task_id} not found"}
    
    doc_ref.update({
        "status": new_status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    logging.info(f"[update_task_status] Task {task_id} → {new_status}")
    return {"success": True, "task_id": task_id, "new_status": new_status}


def get_overdue_tasks(tool_context: ToolContext) -> dict:
    """
    Returns all tasks with status 'pending' whose due_date is in the past.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    docs = db.collection("tasks").where("status", "==", "pending").limit(100).stream()
    overdue = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        due = d.get("due_date", "")
        if due and due < today:
            overdue.append(d)
    logging.info(f"[get_overdue_tasks] Found {len(overdue)} overdue tasks")
    return {"success": True, "overdue_tasks": overdue, "count": len(overdue)}

# ─── Notes tools ──────────────────────────────────────────────

def add_note(
    tool_context: ToolContext,
    client_name: str,
    project_name: str,
    content: str,
) -> dict:
    """
    Adds a note for a client project in Firestore.
    """
    doc_ref = db.collection("notes").document()
    doc_ref.set({
        "client_name": client_name.strip().lower(),
        "project_name": project_name.strip().lower(),
        "content": content.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archived": False,
    })
    logging.info(f"[add_note] Note {doc_ref.id} for {client_name}/{project_name}")
    return {"success": True, "note_id": doc_ref.id}


def get_notes(
    tool_context: ToolContext,
    client_name: str,
) -> dict:
    """
    Retrieves all notes for a given client.
    """
    docs = (
        db.collection("notes")
        .where("client_name", "==", client_name.strip().lower())
        .where("archived", "==", False)
        .limit(20)
        .stream()
    )
    notes = [{"id": d.id, **d.to_dict()} for d in docs]
    logging.info(f"[get_notes] Found {len(notes)} notes for {client_name}")
    return {"success": True, "notes": notes, "count": len(notes)}


def archive_project_notes(
    tool_context: ToolContext,
    client_name: str,
    project_name: str,
) -> dict:
    """
    Archives all notes for a project to Cloud Storage and marks them archived in Firestore.
    Returns the GCS path of the archive file.
    """
    docs = (
        db.collection("notes")
        .where("client_name", "==", client_name.strip().lower())
        .where("project_name", "==", project_name.strip().lower())
        .stream()
    )
    data = []
    doc_refs = []
    for doc in docs:
        data.append({"id": doc.id, **doc.to_dict()})
        doc_refs.append(doc.reference)

    if not data:
        return {"success": True, "archived_count": 0, "note": "No notes found to archive"}

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    blob_name = f"archives/{client_name}/{project_name}_{timestamp}.json"
    storage_client = storage.Client()
    bucket = storage_client.bucket(_bucket_name())
    bucket.blob(blob_name).upload_from_string(
        json.dumps(data, indent=2, default=str),
        content_type="application/json",
    )
    for ref in doc_refs:
        ref.update({"archived": True})

    gcs_path = f"gs://{_bucket_name()}/{blob_name}"
    logging.info(f"[archive_project_notes] Archived {len(data)} notes to {gcs_path}")
    return {"success": True, "archived_count": len(data), "gcs_path": gcs_path}

# ─── Calendar tools ────────────────────────────────────────────

def create_calendar_event(
    tool_context: ToolContext,
    title: str,
    start_datetime: str,
    description: str = "",
    duration_minutes: int = 60,
) -> dict:
    """
    Creates a real event in Google Calendar.
    start_datetime must be ISO format: YYYY-MM-DDTHH:MM:SS  (e.g. 2025-06-20T14:00:00)
    duration_minutes defaults to 60.
    Returns the event ID and a link to the event.
    """
    try:
        service = _calendar_service()
        start = datetime.fromisoformat(start_datetime)
        end = start + timedelta(minutes=duration_minutes)
        event_body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end":   {"dateTime": end.isoformat(),   "timeZone": "UTC"},
        }
        result = service.events().insert(calendarId="primary", body=event_body).execute()
        event_id = result.get("id", "")
        event_link = result.get("htmlLink", "")
        logging.info(f"[create_calendar_event] Created event {event_id}: {title}")
        tool_context.state["last_calendar_event"] = event_id
        return {
            "success": True,
            "event_id": event_id,
            "title": title,
            "start": start.isoformat(),
            "calendar_link": event_link,
        }
    except Exception as e:
        logging.error(f"[create_calendar_event] Error: {e}")
        return {"success": False, "error": str(e)}


def get_upcoming_events(
    tool_context: ToolContext,
    max_results: int = 5,
) -> dict:
    """
    Returns the next upcoming events from Google Calendar.
    """
    try:
        service = _calendar_service()
        now = datetime.utcnow().isoformat() + "Z"
        result = service.events().list(
            calendarId="primary",
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = [
            {
                "title": e.get("summary", "(no title)"),
                "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date")),
                "event_id": e.get("id"),
            }
            for e in result.get("items", [])
        ]
        return {"success": True, "events": events, "count": len(events)}
    except Exception as e:
        logging.error(f"[get_upcoming_events] Error: {e}")
        return {"success": False, "error": str(e)}
