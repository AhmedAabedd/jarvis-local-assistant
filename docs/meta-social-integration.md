# Meta social integration

This document describes Mounir's official-only integration for Facebook,
Messenger, Instagram, Threads, and WhatsApp. It also explains what is deliberately
not implemented so the interface never promises an unofficial capability.

## The simple mental model

OAuth and agent tools solve different problems:

1. **OAuth connects an account.** The user signs in on Meta, grants selected
   permissions, and Meta gives Mounir a token. Mounir performs the code exchange
   on the server and never sends the app secret or token to the browser.
2. **The provider adapter calls Meta.** Small Python operations call the official
   Graph API host for the selected app and API version.
3. **LangGraph tools expose safe operations to an agent.** Each Meta app has its
   own built-in specialist. Read actions are safe; publishing, ad delivery changes,
   and WhatsApp replies use Mounir's confirmation gate.
4. **MCP is optional.** A future remote process could expose the same operations
   over MCP, but OAuth does not require MCP and the current first-party integration
   has no reason to add that extra process boundary.

## What is implemented

| App | Connection | Current agent operations | Explicit exclusions |
|---|---|---|---|
| Facebook | Facebook Login; discovers Pages and, when requested, ad accounts | List accounts, read Page posts, publish Page posts, read ad campaigns, pause/activate a campaign | Groups, personal profiles, scraping, personal DMs |
| Messenger | Facebook Login; discovers eligible Pages | List accounts and explain enforced messaging readiness | Personal Messenger, cold DMs; sending waits for signed inbound-webhook and service-window tracking |
| Instagram | Instagram Login or Facebook Login; professional accounts only | List accounts, read media, publish a public-URL image | Personal accounts, scraping, cold DMs |
| Threads | Threads OAuth with its own app credentials | List profiles, read posts, publish text | Password/browser automation, scraping, unsolicited messages |
| WhatsApp | Separate Cloud API business connections and a signed webhook per sender number | List business inbox conversations, read persisted messages, send/reply with text, and send URL or local-file attachments during the open 24-hour service window | Cold DMs, arbitrary recipients, and template-initiated sends until durable opt-in enforcement exists |

Some real Meta permissions—comments, insights, Messenger/Instagram conversations,
and Threads reply management—are displayed as **not exposed yet**. They cannot be
selected. This distinction matters: an official permission is not the same as a
finished, policy-safe agent action.

WhatsApp displays its token permissions separately from its agent capabilities.
`whatsapp_business_messaging` and `whatsapp_business_management` are required and
are verified against the supplied token when the connection is tested.
Capability choices are persisted per business sender and enforced again when the
agent calls a tool. Official but unfinished capabilities remain visible as **not
exposed yet** and cannot be selected.

Instagram publishing currently supports a single image at a public HTTP(S) URL.
Meta fetches the media itself, so a local file path cannot be sent directly.

## Storage and multiple accounts

`meta_connections` stores any number of app connections per platform. Each record
has its own app ID, app secret or environment reference, API version, OAuth token,
requested capability set, and status. `meta_accounts` stores the Pages,
professional profiles, Threads profiles, and ad accounts discovered for a
connection. A user can disable one discovered account without deleting the whole
connection, and that choice survives account refresh.

Secrets are kept in the local Mounir database with owner-only file permissions and
are removed from all normal API responses. App secrets may be configured as an
environment reference such as `$META_APP_SECRET`. This follows Mounir's existing
universal configuration model rather than hard-coding one developer app or account.

## OAuth flow and security

For every connection Mounir:

1. Builds the official authorization URL from the platform manifest.
2. Generates a random state value, saves only its SHA-256 digest, and expires it
   after ten minutes.
3. Validates the one-time state on callback.
4. Exchanges the authorization code on the server.
5. Attempts the platform's long-lived-token exchange while retaining a valid
   short-lived token if that optional exchange is unavailable.
6. Discovers eligible accounts through the official API and saves them locally.

An incorrect callback does not consume a legitimate state value, preventing a
simple denial-of-service against a pending login. The exact callback URL shown in
the UI must be registered in the Meta developer app. Public deployments normally
need HTTPS; the official Threads sample specifically requires an HTTPS redirect and
does not use a plain localhost callback.

The capability selector requests only the scopes needed by enabled operations.
Meta can still require Business Verification, App Review, Advanced Access, test
users, or asset roles before a real account grants those permissions. Those are
Meta-side requirements, not something Mounir can bypass.

## Agent safety boundaries

- An account ID must come from Mounir's discovered, enabled account list.
- Every provider operation verifies that the account belongs to the expected app,
  its connection is enabled and connected, and the capability was granted to the
  connection configuration.
- Facebook ad campaign changes first verify that the campaign belongs to the
  selected ad account.
- Outbound publishing and campaign changes require confirmation.
- WhatsApp agent destinations must already exist in the signed business inbox.
  Every send rechecks the stored inbound conversation and rejects the action after
  24 hours. Outbound text, replies, and attachments require confirmation.
- The private paired WhatsApp channel is a separate transport. Its credentials,
  webhook, paired controller, conversation history, and activation state are not
  read by the WhatsApp business specialist.
- Messenger and Instagram messaging sends are not exposed until Mounir has signed
  webhook ingestion, inbound identity state, deduplication, and messaging-window
  enforcement for those platforms.

## Setup

1. Create the appropriate app/use case in the Meta developer dashboard. Threads
   uses Threads-specific app credentials; they are not interchangeable with a
   regular Facebook app ID and secret.
2. Open **Agent Studio → Meta**, choose the app tab, and add a connection.
3. Select only the capabilities needed by this installation.
4. Save, copy the displayed callback URL, and register that exact URL in Meta.
5. Choose **Connect with OAuth**, complete Meta sign-in, and return to Mounir.
6. Use **Test and refresh** to confirm the token and refresh the eligible account
   list. Disable any account the agents must not use.
7. In **Subagents**, assign a configured language model and review confirmation
   rules for the Facebook, Messenger, Instagram, Threads, and WhatsApp specialists.

WhatsApp has two independent setup locations:

- **Connections → WhatsApp** configures the private paired chat channel, like
  Telegram. It is not an agent connection.
- **Meta → WhatsApp** configures one or more business sender numbers for the
  WhatsApp specialist. Each connection has its own credentials, signed webhook,
  persisted inbox, and activation state.

## Verified primary references

- [Meta's official Instagram API collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api) documents professional-account limitations, both login modes, publishing permissions, public media hosting, and the media-container publishing flow.
- [Meta's official Messenger Platform webhook collection](https://www.postman.com/meta/messenger-platform-api/folder/22794852-b5d97624-14d8-4e67-a2e4-529add49ca58) documents Page installation, inbound webhooks, and `pages_messaging` / `pages_manage_metadata` requirements.
- [Meta's official Threads API collection](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api) documents OAuth-backed profile, publishing, and read operations.
- [Meta's official Threads sample](https://github.com/fbsamples/threads_api) confirms that Threads app credentials are distinct and that its OAuth callback must be registered over HTTPS.
- [Meta's official Facebook Marketing API workspace](https://www.postman.com/meta/facebook-marketing-api/overview) documents campaign read/manage operations and the required ad-account/token setup.
- [Meta's official WhatsApp Business Platform workspace](https://www.postman.com/meta/whatsapp-business-platform/overview) identifies Cloud API as the official API for sending and receiving WhatsApp business messages.

Meta's developer documentation is the final authority. API versions and review
requirements change, which is why the version is configurable per connection and
provider-specific behavior stays isolated in `mounir/meta_social.py` and
`mounir/whatsapp_business.py`.
