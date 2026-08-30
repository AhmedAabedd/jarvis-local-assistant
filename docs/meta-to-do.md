# Meta integration to-do

These options are real capabilities provided by Meta, but they are intentionally
disabled in Mounir until the corresponding tools are complete and policy-safe.

“Disabled” does **not** mean that Meta rejected the account. It means:

> Meta supports the capability, but Mounir does not safely expose it yet.

Mounir must not request the associated OAuth permissions until the requirements
below are implemented and tested.

## Facebook

### Page engagement management

Official capability: manage Page comments and engagement.

Planned tools:

- List comments on a Page post.
- Reply to a comment as the Page.
- Hide or unhide a comment where supported.
- Delete a comment where supported.

Required before enabling:

- Verify Page ownership and enabled-account selection on every call.
- Implement pagination and stable post/comment identifiers.
- Require confirmation for hide, unhide, and delete actions.
- Test permission failures and content that Meta no longer exposes.

### Page insights

Official capability: read Page performance metrics.

Planned tools:

- List metrics supported for the selected Page and API version.
- Read metrics for an explicit date range.

Required before enabling:

- Discover supported metrics dynamically instead of assuming one fixed catalog.
- Validate date ranges and handle unavailable or deprecated metrics clearly.
- Keep all insight operations read-only.

## Messenger

### Page conversations

Official capability: receive and reply to conversations involving a Facebook Page.

Planned tools:

- List eligible Page conversations.
- Read messages in a selected conversation.
- Reply inside an eligible conversation.

Required before enabling:

- Add a signed Messenger webhook endpoint.
- Subscribe only selected Pages to the required webhook fields.
- Persistently deduplicate webhook event/message IDs.
- Track the Page, sender identity, last inbound message, and allowed messaging window.
- Make the reply tool accept a stored conversation ID—not an arbitrary recipient ID.
- Enforce Meta’s current messaging-policy window and permitted message tags.
- Require confirmation for every outbound reply.

There must never be a generic cold-DM tool such as
`send_message(recipient_id, text)`.

## Instagram

### Comment management

Official capability: manage comments on professional-account media.

Planned tools:

- List comments on selected media.
- Reply to a comment.
- Hide, unhide, or delete a comment where supported.

Required before enabling:

- Support both Instagram Login and Facebook Login endpoint/token differences.
- Validate that the media belongs to the selected professional account.
- Require confirmation for moderation changes.

### Customer messages

Official capability: manage conversations for an Instagram professional account.

Planned tools:

- List eligible conversations.
- Read messages in a selected conversation.
- Reply to a conversation initiated through the professional account.

Required before enabling:

- Add signed Instagram messaging webhooks.
- Track inbound Instagram-scoped user IDs and conversation eligibility.
- Persistently deduplicate events and message deliveries.
- Enforce Meta’s current messaging window and policy restrictions.
- Prevent arbitrary recipient IDs and cold automated DMs.
- Require confirmation for every outbound reply.

### Instagram insights

Official capability: read professional-account and media metrics.

Planned tools:

- Discover metrics available to the selected account/media type.
- Read account or media metrics for a validated time range.

Required before enabling:

- Handle differences between Instagram Login and Facebook Login permissions.
- Discover or validate metrics instead of shipping a stale fixed list.
- Keep insight operations read-only.

## Threads

### Read replies

Official capability: read replies involving the connected Threads profile.

Planned tools:

- List replies to a selected post.
- Read replies created by the connected profile.

Required before enabling:

- Implement pagination and exact post ownership checks.
- Distinguish posts, replies, quotes, and reposts correctly.

### Manage replies

Official capability: manage replies where the Threads API permits it.

Planned tools:

- Reply to an eligible post.
- Hide or unhide a reply where supported.
- Delete the connected profile’s own reply where supported.

Required before enabling:

- Validate ownership and operation eligibility with the API.
- Require confirmation for every publish, hide, unhide, or delete action.

### Threads insights

Official capability: read profile and media metrics.

Planned tools:

- Discover supported profile/post metrics.
- Read metrics for a validated time range.

Required before enabling:

- Handle unavailable metrics and API-version changes explicitly.
- Keep insight operations read-only.

## WhatsApp Business agent

The private paired WhatsApp channel is not part of this list. It is a
Connectivity transport like Telegram and remains configured independently under
**Connections → WhatsApp**. The items below apply only to the WhatsApp specialist
under **Meta → WhatsApp**.

### Template-initiated and reopened conversations

Official capability: send an approved template to an opted-in recipient when no
24-hour customer-service window is open.

Planned tools:

- Discover approved templates and their languages/components dynamically.
- Send a selected template to a stored business contact.
- Show template delivery status and provider rejection details.

Required before enabling:

- Add a durable business-contact and consent/opt-in record, including source and
  timestamp.
- Require the destination to come from that stored contact record—not arbitrary
  agent-generated input.
- Validate template parameters against the currently approved template schema.
- Require confirmation showing the sender number, recipient, template, and
  resolved parameters.
- Handle category, region, quality, pacing, and API-version restrictions without
  assuming one installation's policy state.

Until these requirements are complete, Mounir intentionally allows free-form
text and attachments only for contacts discovered from signed inbound messages
whose rolling 24-hour service window is still open.

### Inbound media retrieval and richer message types

Official capability: receive media identifiers and supported interactive,
location, contact, reaction, and other message payloads through webhooks.

Planned tools:

- Download an inbound media item on explicit request using its short-lived Meta
  media URL.
- Read and summarize supported interactive, location, contact, and reaction
  messages without flattening away important fields.
- Send additional official interactive message types where the current account,
  region, and conversation state permit them.

Required before enabling:

- Store structured message metadata instead of inventing a universal text shape.
- Enforce media size, MIME-type, expiry, and local-storage limits.
- Require confirmation for every outbound attachment or interactive action.
- Test webhook redelivery and delivery-status events that race the original send.

## Shared acceptance checklist

A disabled capability can be enabled only when all applicable items are complete:

- The operation exists in Meta’s current official documentation.
- OAuth requests only the minimum official permissions required.
- Mounir verifies the selected connection and enabled account for every call.
- Provider-specific behavior stays isolated in the Meta adapter.
- Incoming webhooks are signature-verified and persistently deduplicated.
- Messaging tools can target only a stored, eligible inbound conversation.
- Public, destructive, or outbound actions use Mounir’s confirmation gate.
- Rate limits, pagination, token expiry, and API errors are handled clearly.
- Unit tests cover successful calls, missing permissions, wrong-account access,
  expired windows, duplicate webhooks, and declined confirmation.
- The UI clearly distinguishes configured permissions from tools that are actually
  available to agents.

## Permanent exclusions

These are not to-do items and must not be added through unofficial workarounds:

- Facebook Groups API replacements based on scraping or browser automation.
- Facebook personal-profile automation.
- Instagram personal-account automation.
- Personal Messenger automation.
- Cold automated DMs or arbitrary-recipient messaging.
- Password/session-cookie automation and scraping.
