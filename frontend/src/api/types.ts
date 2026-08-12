export type Id = number

export interface Profile {
  user_name: string
  assistant_name: string
  location: string
  preferred_language: 'auto' | 'en' | 'fr' | 'ar' | string
}

export interface ModelRecord {
  id: Id
  name: string
  model: string
  provider: string
  base_url: string
  api_key?: string
  api_key_configured?: boolean
}

export interface ToolInfo {
  name: string
  description?: string
  requires_confirmation?: boolean
  selected?: boolean
}

export interface McpServer {
  id: Id
  name: string
  description: string
  transport: 'stdio' | 'streamable_http' | 'sse'
  connection: string
  headers: string | Record<string, string>
  env: string | Record<string, string>
  auth_scheme?: string
  headers_configured?: boolean
  env_configured?: boolean
  credentials_configured?: boolean
  setup_type?: string
  connection_status?: string
  tool_count?: number
  last_error?: string
}

export interface Subagent {
  id: Id
  name: string
  description: string
  system_prompt: string
  model_id: Id
  mcp_server_id: Id
  model?: string
  model_name?: string
  mcp_server_name?: string
  confirm_tool_calls: boolean
  confirm_tools: string[] | string
  dedupe_tools: string[] | string
  enabled: boolean | number
  has_icon?: boolean
  parent_agent_id?: Id | null
  parent_name?: string
  parent_agent_ids?: Id[]
  parent_node_ids?: Id[]
  parent_names?: string[]
  connected_to_supervisor?: boolean
  child_agent_ids?: Id[]
  child_count?: number
  placements?: AgentPlacement[]
}

export interface AgentPlacement {
  id: Id
  agent_id: Id
  parent_node_id: Id | null
  parent_agent_id: Id | null
  parent_name: string
  depth: number
  path_names: string[]
  path_label: string
  enabled_tools: string[] | null
  child_node_ids: Id[]
  child_agent_ids: Id[]
}

export interface SubagentNodeRelation {
  id: Id
  subagent_id: Id
  name: string
  enabled?: boolean
  has_icon?: boolean
  path_label?: string
}

export interface SubagentNode {
  id: Id
  subagent_id: Id
  parent_node_id: Id | null
  created_at: string
  depth: number
  path_names: string[]
  path_label: string
  enabled_tools: string[] | null
  parent: SubagentNodeRelation | null
  subagent: {
    id: Id
    name: string
    description: string
    model_id: Id
    model_name: string
    model: string
    mcp_server_id: Id
    mcp_server_name: string
    enabled: boolean
    has_icon: boolean
  }
  children: SubagentNodeRelation[]
}

export interface BuiltinAgent {
  key: string
  name: string
  description: string
  purpose?: string
  model?: string
  model_id?: Id
  provider?: string
  enabled: boolean
  tools?: ToolInfo[]
  model_options?: Array<{ id: Id; model: string; label: string }>
}

export interface Supervisor {
  name: string
  description: string
  model?: string
  model_id?: Id
  provider?: string
  tools: ToolInfo[]
  model_options?: Array<{ id: Id; model: string; label: string }>
}

export interface AgentOverview {
  supervisor: Supervisor
  builtins: BuiltinAgent[]
}

export interface VoiceProvider {
  provider: string
  model: string
  voice?: string
  language: string
  base_url: string
  api_key_configured?: boolean
}
export interface VoiceSettings {
  stt: VoiceProvider
  tts: VoiceProvider
}

export interface TelegramSettings {
  enabled: boolean
  token_configured: boolean
  paired: boolean
  running?: boolean
  connection_status: string
  bot_username?: string
  chat_name?: string
  chat_username?: string
  last_error?: string
}

export interface WhatsAppSettings {
  enabled: boolean
  credentials_configured: boolean
  token_configured?: boolean
  app_secret_configured?: boolean
  paired: boolean
  paired_phone?: string
  paired_phone_hint?: string
  paired_name?: string
  webhook_verified: boolean
  connection_status: string
  phone_number_id?: string
  business_account_id?: string
  api_version?: string
  heartbeat_template_name?: string
  heartbeat_template_language?: string
  verify_token?: string
  webhook_path?: string
  display_phone_number?: string
  verified_name?: string
  last_error?: string
}

export interface HeartbeatCapability {
  key: string
  id?: string
  name: string
  description?: string
  tools: ToolInfo[]
}
export interface HeartbeatRun {
  id: number
  status: string
  trigger?: string
  started_at?: string
  finished_at?: string
  summary?: string
  error?: string
}
export interface HeartbeatSettings {
  enabled: boolean
  interval_minutes: number
  instructions: string
  notify_telegram: boolean
  notify_whatsapp: boolean
  capabilities: HeartbeatCapability[]
  recent_runs: HeartbeatRun[]
}

export interface ServerToolsState {
  status: string
  tools: ToolInfo[]
  tested_at?: string
  error?: string
}

export interface SetupDescriptor {
  title: string
  description: string
  status: { text: string; kind: string }
  file_actions: Array<{ id: string; label: string; accept: string; busy_label: string }>
  actions: Array<{
    id: string
    label: string
    busy_label: string
    disabled: boolean
    style: string
  }>
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}
export interface Notification {
  id?: number
  message?: string
  content?: string
  created_at?: string
  read_at?: string | null
}
