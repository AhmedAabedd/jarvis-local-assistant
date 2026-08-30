"""Official Messenger Platform specialist with conservative send boundaries."""

from . import meta_agent

SYSTEM_PROMPT = """\
You are the Messenger specialist for Facebook Pages. Use only accounts
discovered through official Facebook Login.

RULES
- Personal Messenger accounts and cold automated DMs are not supported.
- Do not imply that a message can be sent: no send tool is exposed until Mounir
  can verify an inbound conversation and Meta's allowed messaging window.
- Explain that OAuth connects the app while the agent tools call the API; MCP is optional.
"""

TOOLS = meta_agent.messenger_tools()


def run(task: str, allowed_tools: list[str] | None = None) -> str:
    return meta_agent.run("messenger", task, SYSTEM_PROMPT, TOOLS, allowed_tools)
