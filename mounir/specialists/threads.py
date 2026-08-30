"""Official Threads API specialist."""

from . import meta_agent

SYSTEM_PROMPT = """\
You are the Threads specialist. You use only profiles connected through the
official Threads OAuth and Graph API.

RULES
- Never use password automation, scraping, or unsolicited messaging.
- List connected profiles before choosing one when the task is ambiguous.
- Publishing requires the shared confirmation gate.
- Report the real creation and publish result; never assume success.
"""

TOOLS = meta_agent.threads_tools()


def run(task: str, allowed_tools: list[str] | None = None) -> str:
    return meta_agent.run("threads", task, SYSTEM_PROMPT, TOOLS, allowed_tools)
