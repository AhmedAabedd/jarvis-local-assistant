"""Shared runtime and typed tools for the official Meta specialists."""

from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.tools import BaseTool, tool

from .. import agent_skills, config, graph_runtime, llm, meta_social, whatsapp_business

MAX_TOOL_ROUNDS = 6


def account_tool(platform: str) -> BaseTool:
    @tool("list_connected_accounts")
    def list_connected_accounts_tool() -> list[dict]:
        """List enabled accounts discovered through the configured official API connection."""

        return meta_social.enabled_accounts(platform)

    return list_connected_accounts_tool


def facebook_tools() -> list[BaseTool]:
    @tool("list_page_posts")
    def list_page_posts_tool(
        account_id: Annotated[int, "Mounir's numeric ID for a connected Facebook Page."],
        limit: Annotated[int, "Number of recent posts, from 1 to 25."] = 10,
    ) -> list[dict]:
        """Read recent posts from an enabled Facebook Page."""

        return meta_social.facebook_page_posts(account_id, limit)

    @tool("publish_page_post")
    def publish_page_post_tool(
        account_id: Annotated[int, "Mounir's numeric ID for a connected Facebook Page."],
        message: Annotated[str, "Exact post text. May be empty when a link is provided."],
        link: Annotated[str, "Optional public http(s) link to attach."] = "",
    ) -> dict:
        """Publish a post as an enabled Facebook Page after user confirmation."""

        return meta_social.publish_facebook_page_post(account_id, message, link)

    @tool("list_ad_campaigns")
    def list_ad_campaigns_tool(
        account_id: Annotated[int, "Mounir's numeric ID for a connected Meta ad account."],
        limit: Annotated[int, "Number of campaigns, from 1 to 50."] = 25,
    ) -> list[dict]:
        """Read campaigns and delivery status from an enabled Meta ad account."""

        return meta_social.facebook_ad_campaigns(account_id, limit)

    @tool("set_ad_campaign_status")
    def set_ad_campaign_status_tool(
        account_id: Annotated[int, "Mounir's numeric ID for the campaign's Meta ad account."],
        campaign_id: Annotated[str, "Exact campaign ID returned by list_ad_campaigns."],
        status: Annotated[Literal["ACTIVE", "PAUSED"], "New campaign delivery status."],
    ) -> dict:
        """Activate or pause an existing Meta ad campaign after user confirmation."""

        return meta_social.set_facebook_ad_campaign_status(account_id, campaign_id, status)

    return [
        account_tool("facebook"),
        list_page_posts_tool,
        publish_page_post_tool,
        list_ad_campaigns_tool,
        set_ad_campaign_status_tool,
    ]


def messenger_tools() -> list[BaseTool]:
    @tool("messaging_policy")
    def messaging_policy_tool() -> str:
        """Explain the enforced Messenger messaging boundary for this integration."""

        return (
            "Mounir does not expose a Messenger send tool yet. Official Page replies "
            "require a verified webhook and an inbound user conversation so the allowed "
            "messaging window can be enforced; personal accounts and cold DMs are excluded."
        )

    return [account_tool("messenger"), messaging_policy_tool]


def instagram_tools() -> list[BaseTool]:
    @tool("list_media")
    def list_media_tool(
        account_id: Annotated[int, "Mounir's numeric ID for an Instagram professional account."],
        limit: Annotated[int, "Number of recent media items, from 1 to 25."] = 10,
    ) -> list[dict]:
        """Read recent media from an enabled Instagram professional account."""

        return meta_social.instagram_media(account_id, limit)

    @tool("publish_image")
    def publish_image_tool(
        account_id: Annotated[int, "Mounir's numeric ID for an Instagram professional account."],
        image_url: Annotated[str, "Publicly reachable http(s) URL for the image."],
        caption: Annotated[str, "Exact caption text."] = "",
    ) -> dict:
        """Publish one public-URL image to an Instagram professional account after confirmation."""

        return meta_social.publish_instagram_image(account_id, image_url, caption)

    return [account_tool("instagram"), list_media_tool, publish_image_tool]


def threads_tools() -> list[BaseTool]:
    @tool("list_posts")
    def list_posts_tool(
        account_id: Annotated[int, "Mounir's numeric ID for a connected Threads profile."],
        limit: Annotated[int, "Number of recent posts, from 1 to 25."] = 10,
    ) -> list[dict]:
        """Read recent posts from an enabled Threads profile."""

        return meta_social.threads_posts(account_id, limit)

    @tool("publish_text")
    def publish_text_tool(
        account_id: Annotated[int, "Mounir's numeric ID for a connected Threads profile."],
        text: Annotated[str, "Exact text to publish."],
    ) -> dict:
        """Publish a text post to Threads after user confirmation."""

        return meta_social.publish_threads_text(account_id, text)

    return [account_tool("threads"), list_posts_tool, publish_text_tool]


def whatsapp_tools() -> list[BaseTool]:
    @tool("list_business_connections")
    def list_business_connections_tool() -> list[dict]:
        """List enabled WhatsApp Business senders configured for this agent."""

        return whatsapp_business.list_connections()

    @tool("list_conversations")
    def list_conversations_tool(
        connection_id: Annotated[int, "Mounir's numeric WhatsApp Business connection ID."],
    ) -> list[dict]:
        """List known inbox contacts and whether each 24-hour service window is open."""

        return whatsapp_business.list_conversations(connection_id)

    @tool("read_messages")
    def read_messages_tool(
        connection_id: Annotated[int, "Mounir's numeric WhatsApp Business connection ID."],
        contact_phone: Annotated[str, "An exact contact phone returned by list_conversations."],
        limit: Annotated[int, "Number of recent messages, from 1 to 200."] = 50,
    ) -> list[dict]:
        """Read persisted messages for a known WhatsApp Business conversation."""

        return whatsapp_business.read_messages(connection_id, contact_phone, limit)

    @tool("send_message")
    def send_message_tool(
        connection_id: Annotated[int, "Mounir's numeric WhatsApp Business connection ID."],
        contact_phone: Annotated[str, "An exact contact phone returned by list_conversations."],
        message: Annotated[str, "Exact text to send."],
    ) -> dict:
        """Send text to a known contact during the open 24-hour service window after confirmation."""

        return whatsapp_business.send_text(connection_id, contact_phone, message)

    @tool("reply_to_message")
    def reply_to_message_tool(
        connection_id: Annotated[int, "Mounir's numeric WhatsApp Business connection ID."],
        message_id: Annotated[str, "Exact inbound message ID returned by read_messages."],
        message: Annotated[str, "Exact reply text."],
    ) -> dict:
        """Reply to a stored inbound message during its open service window after confirmation."""

        return whatsapp_business.reply_to_message(connection_id, message_id, message)

    @tool("send_attachment")
    def send_attachment_tool(
        connection_id: Annotated[int, "Mounir's numeric WhatsApp Business connection ID."],
        contact_phone: Annotated[str, "An exact contact phone returned by list_conversations."],
        source: Annotated[str, "Public http(s) URL or exact local file path."],
        caption: Annotated[str, "Optional caption for supported media types."] = "",
    ) -> dict:
        """Send a URL or local attachment in an open service window after confirmation."""

        return whatsapp_business.send_attachment(
            connection_id, contact_phone, source, caption
        )

    return [
        list_business_connections_tool,
        list_conversations_tool,
        read_messages_tool,
        send_message_tool,
        reply_to_message_tool,
        send_attachment_tool,
    ]


def run(
    key: str,
    task: str,
    system_prompt: str,
    tools: list[BaseTool],
    allowed_tools: list[str] | None = None,
) -> str:
    from .. import db

    runtime = db.get_builtin_agent_runtime(
        key,
        fallback_model=config.SYSTEM_MODEL,
        fallback_base_url=config.NVIDIA_BASE_URL,
        fallback_api_key=config.NVIDIA_API_KEY,
        fallback_provider="NVIDIA",
    )
    selected_tools = graph_runtime.select_tools(tools, allowed_tools)
    skill_prompt, skill_tool = agent_skills.runtime_access("builtin", key)
    if skill_tool is not None:
        selected_tools.append(skill_tool)
    messages = [
        {"role": "system", "content": config.specialist_system_prompt(system_prompt)},
    ]
    if skill_prompt:
        messages.append({"role": "system", "content": skill_prompt})
    messages.append({"role": "user", "content": task})
    return graph_runtime.run_tool_agent(
        messages,
        selected_tools,
        lambda history, schemas: llm.openai_chat(
            history,
            tools=schemas,
            model=runtime["model"],
            provider=runtime["provider"],
            base_url=runtime["base_url"],
            api_key=runtime["api_key"],
        ),
        max_rounds=MAX_TOOL_ROUNDS,
        empty_response=f"The {key} agent had nothing to report.",
        exhausted_response=f"The {key} agent reached its tool-round limit; partial result only.",
        error_formatter=lambda _executed, error: f"The {key} agent failed: {error}",
        confirmation_tools=db.get_builtin_confirmation_tools(key),
    )
