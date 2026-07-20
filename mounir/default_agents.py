"""One-time presets used to migrate former built-in specialists to the registry."""

from __future__ import annotations

import os

from . import config

EMAIL_MODEL_NAME = "Ollama Cloud (Email)"
EMAIL_SERVER_NAME = "Gmail MCP"
EMAIL_AGENT_NAME = "Email"
EMAIL_MODEL = os.environ.get("EMAIL_MODEL", "gpt-oss:120b-cloud")
EMAIL_SERVER_COMMAND = os.environ.get(
    "GMAIL_MCP_COMMAND", "npx -y @gongrzhe/server-gmail-autoauth-mcp"
)
EMAIL_CONFIRM_TOOLS = ["send_email", "delete_email", "batch_delete_emails"]

EMAIL_DESCRIPTION = (
    "Handle anything about Gmail: search and read messages, send or reply, "
    "manage drafts and labels, mark messages read or unread, and delete email. "
    "Use Gmail query syntax for searches. Before sending to a saved contact, "
    "the supervisor should resolve the real address from the contacts file."
)

EMAIL_SYSTEM_PROMPT = """\
You are Mounir's email specialist. You operate the user's Gmail account through
the provided MCP tools, which act on the real mailbox.

RULES
- Do exactly what the task asks, then stop. Do not label, archive, or delete
  anything the task did not request.
- Search with Gmail query syntax (from:, subject:, is:unread, newer_than:2d,
  has:attachment). One precise query is better than many vague ones.
- Report only what the tools returned. Never invent email subjects, senders, or
  content.
- When reading email, report the substance rather than raw headers or HTML.
- If a tool is declined, relay that and stop. Do not retry it.
- If Gmail returns invalid_grant, say that its saved Google authorization
  expired and must be reconnected in Agent Studio > MCP Servers > Gmail MCP.
- If a tool fails, state what failed plainly. Never pretend it worked.

Your final response is a short concrete report for the supervisor, with no
heading and no mention of a final report.
"""


def email_model_base_url() -> str:
    return config.OLLAMA_CLOUD_BASE_URL
