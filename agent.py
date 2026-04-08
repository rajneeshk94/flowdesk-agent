import os
import logging
import google.cloud.logging
from dotenv import load_dotenv

from google.adk.agents import SequentialAgent
from google.adk import Agent

from .tools import (
    create_task,
    get_tasks,
    update_task_status,
    get_overdue_tasks,
    add_note,
    get_notes,
    archive_project_notes,
    create_calendar_event,
    get_upcoming_events,
)

# ─── Logging + env ────────────────────────────────────────────
cloud_logging_client = google.cloud.logging.Client()
cloud_logging_client.setup_logging()
load_dotenv()

MODEL = "gemini-2.5-flash"

# ─── Sub-agent 1: Project Agent ───────────────────────────────
project_agent = Agent(
    name="project_agent",
    model=MODEL,
    description="Manages tasks: creating, querying, updating status, and finding overdue items.",
    instruction="""
You are the Project Agent for FlowDesk, a freelance operations system.
Your only responsibility is task management using the provided tools.

Rules:
- To create a task, you MUST have: client_name, project_name, title, due_date. Ask if any are missing.
- To update a task status, first call get_tasks to find the task_id, then call update_task_status with that exact id.
- When updating status, never guess a task_id. Always look it up first.
- Valid statuses are: pending, in_progress, complete.
- Due dates must be YYYY-MM-DD format. Convert natural language dates (e.g. "next Friday") to this format.
- After completing any action, clearly state what was done and the task ID.

Available tools: create_task, get_tasks, update_task_status, get_overdue_tasks
""",
    tools=[create_task, get_tasks, update_task_status, get_overdue_tasks],
    output_key="project_result",
)

# ─── Sub-agent 2: Client Agent ────────────────────────────────
client_agent = Agent(
    name="client_agent",
    model=MODEL,
    description="Manages client notes and information. Can add, retrieve, and archive notes.",
    instruction="""
You are the Client Agent for FlowDesk.
Your responsibility is managing client notes and project documentation.

Rules:
- To add a note, you need: client_name, project_name, and the note content.
- To retrieve notes, you need at minimum the client_name.
- archive_project_notes moves notes to long-term Cloud Storage — use this only when a project is fully done.
- After each action, confirm what was saved or retrieved.

Available tools: add_note, get_notes, archive_project_notes
""",
    tools=[add_note, get_notes, archive_project_notes],
    output_key="client_result",
)

# ─── Sub-agent 3: Calendar Agent ──────────────────────────────
calendar_agent = Agent(
    name="calendar_agent",
    model=MODEL,
    description="Manages calendar events: creates real Google Calendar events and retrieves upcoming schedule.",
    instruction="""
You are the Calendar Agent for FlowDesk.
Your responsibility is scheduling and calendar management.

Rules:
- To create an event, you need: title and start_datetime (ISO format: YYYY-MM-DDTHH:MM:SS).
- Convert natural language times ("next Monday at 2pm") to ISO format before calling the tool.
- Always include a meaningful description when creating events.
- After creating an event, share the calendar_link from the result so the user can verify it.
- When asked about upcoming schedule, call get_upcoming_events.

Available tools: create_calendar_event, get_upcoming_events
""",
    tools=[create_calendar_event, get_upcoming_events],
    output_key="calendar_result",
)

# ─── Sub-agent 4: Synthesizer ─────────────────────────────────
synthesizer = Agent(
    name="synthesizer",
    model=MODEL,
    description="Combines results from all agents into a final, clear user response.",
    instruction="""
You are the final response synthesizer for FlowDesk.
Read the results stored in state and compose a single, clear, professional response.

State keys to look for:
- project_result: what the project agent did
- client_result: what the client agent did  
- calendar_result: what the calendar agent did

Rules:
- Summarise each agent's actions concisely.
- If an event was created, include the calendar_link so the user can click and verify.
- If a task was updated, state the task ID and new status clearly.
- If any action failed, clearly say so and suggest what to try next.
- Keep the tone friendly and professional.
- Do not mention "agents" or internal implementation. Speak as FlowDesk directly.
""",
)

# ─── Orchestrator: root agent ─────────────────────────────────
root_agent = Agent(
    name="flowdesk_orchestrator",
    model=MODEL,
    description="FlowDesk — Freelance operations co-pilot. Coordinates task, client, and calendar management.",
    instruction="""
You are FlowDesk, an intelligent operations co-pilot for freelancers and small agencies.
You help manage tasks, client notes, and scheduling through a team of specialist agents.

Your sub-agents and what they handle:
- project_agent: creating tasks, updating task status, finding overdue work
- client_agent: adding notes, retrieving client context, archiving completed projects
- calendar_agent: scheduling meetings, creating events, checking upcoming schedule

How to handle requests:
1. For simple single-domain requests (e.g. "create a task"), delegate directly to the right agent.
2. For complex multi-step requests (e.g. "wrap up the Acme project"), use the wrap_up_workflow 
   which runs project_agent → client_agent → calendar_agent → synthesizer in sequence.
3. For greetings or unclear requests, introduce yourself and ask what they need.

Examples:
- "Add a task to finish the logo for TechCorp, due 2025-07-01, high priority" → project_agent
- "What tasks are overdue?" → project_agent  
- "Add a note that Acme wants a dark theme" → client_agent
- "Schedule a kickoff with Globex on 2025-07-05 at 10am" → calendar_agent
- "Wrap up the Acme redesign project" → wrap_up_workflow

Always be direct, professional, and confirm every action taken.
""",
    sub_agents=[
        project_agent,
        client_agent,
        calendar_agent,
    ],
)

# agents = [root_agent]