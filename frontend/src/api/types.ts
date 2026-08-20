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
  label?: string
  description?: string
  server_name?: string
  tool_name?: string
  requires_confirmation?: boolean
  selected?: boolean
}

export interface SubagentMcpSource {
  mcp_server_id: Id
  mcp_server_name?: string
  mcp_server_description?: string
  connection_status?: string
  enabled_tools: string[] | null
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
  setup_command?: string
  oauth_connected?: boolean
  headers_configured?: boolean
  env_configured?: boolean
  credentials_configured?: boolean
  setup_configured?: boolean
  credential_files?: McpCredentialFile[]
  connection_status?: string
  tool_count?: number
  last_error?: string
}

export interface McpCredentialFile {
  env_var: string
  filename: string
}

export interface Subagent {
  id: Id
  name: string
  description: string
  system_prompt: string
  model_id: Id
  mcp_server_id: Id | null
  mcp_sources: SubagentMcpSource[]
  mcp_server_count?: number
  model?: string
  model_name?: string
  mcp_server_name?: string
  confirm_tool_calls: boolean
  confirm_tools: string[] | string
  dedupe_tools: string[] | string
  enabled: boolean | number
  has_icon?: boolean
  node_id?: Id
  parent_agent_id?: Id | null
  parent_node_id?: Id | null
  parent_name?: string
  connected_to_supervisor?: boolean
  child_agent_ids?: Id[]
  child_count?: number
  placement_count?: number
  depth?: number
  path_names: string[]
  path_label: string
  enabled_tools: string[] | null
}

export interface SubagentPlacement {
  id: Id
  node_id: Id
  subagent_id: Id
  parent_node_id: Id | null
  parent_agent_id: Id | null
  workflow_id: Id | null
  position: number
  name: string
  description: string
  enabled: boolean
  enabled_tools: string[] | null
  model_id: Id
  model_name: string
  model: string
  mcp_server_id: Id | null
  mcp_server_name: string
  has_icon: boolean
  depth: number
  path_names: string[]
  path_label: string
  created_at: string
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
  workflow_id: Id | null
  position: number
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
    mcp_server_id: Id | null
    mcp_server_name: string
    mcp_sources: SubagentMcpSource[]
    enabled: boolean
    has_icon: boolean
  }
  children: SubagentNodeRelation[]
}

export interface Workflow {
  id: Id
  name: string
  description: string
  system_prompt: string
  model_id: Id | null
  model_name?: string | null
  model?: string | null
  execution_mode: 'agentic' | 'direct'
  node_count: number
  created_at: string
  updated_at: string
}

export interface WorkflowNodePlacement {
  id: Id
  owner_workflow_id: Id | null
  child_workflow_id: Id
  parent_node_id: Id | null
  position: number
  created_at: string
  name: string
  description: string
  execution_mode: 'agentic' | 'direct'
}

export interface BuiltinAgent {
  key: string
  name: string
  description: string
  system_prompt: string
  purpose?: string
  model?: string
  model_id?: Id | null
  generation_model?: string | null
  generation_model_id?: Id | null
  provider?: string
  enabled: boolean
  connected: boolean
  tools?: ToolInfo[]
  model_options?: Array<{ id: Id; model: string; label: string }>
  generation_model_options?: Array<{ id: Id; model: string; label: string }>
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

export interface TtsVoiceOption {
  id: string
  label: string
  group: string
}

export interface TtsVoiceCatalog {
  provider: string
  model: string
  discovery: 'model_manifest' | 'manual'
  voices: TtsVoiceOption[]
}

export interface TelegramSettings {
  enabled: boolean
  reply_mode: 'text' | 'voice'
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
  message?: string
  error?: string
}
export interface HeartbeatToolSelection {
  agent_key: string
  tool_name: string
}
export interface HeartbeatTask {
  id: number
  name: string
  enabled: boolean
  interval_minutes: number
  execution_limit: number
  remaining_runs: number
  instructions: string
  notify_telegram: boolean
  notify_whatsapp: boolean
  selected_agents: string[]
  selected_tools: HeartbeatToolSelection[]
  next_run_at?: string | null
  last_run_at?: string | null
  last_status: string
  last_message?: string
  last_error?: string
  created_at?: string
  updated_at?: string
  recent_runs: HeartbeatRun[]
}
export interface HeartbeatSettings {
  enabled: boolean
  interval_minutes: number
  instructions: string
  notify_telegram: boolean
  notify_whatsapp: boolean
  capabilities: HeartbeatCapability[]
  recent_runs: HeartbeatRun[]
  tasks: HeartbeatTask[]
}

export interface ServerToolsState {
  status: string
  tools: ToolInfo[]
  tested_at?: string
  error?: string
}

export interface SetupDescriptor {
  configured: boolean
  status: { text: string; kind: string }
  oauth: { enabled: boolean; connected: boolean; in_progress: boolean }
  command: { configured: boolean }
  credential_files: McpCredentialFile[]
  error?: string
}

export interface SetupActionResult {
  ok: boolean
  message?: string
  authorization_url?: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  attachments?: ChatAttachment[]
}
export interface ChatAttachment {
  id: string
  filename: string
  mime_type: string
  url: string
}
export interface Notification {
  id?: number
  heartbeat_task_id?: number
  heartbeat_task_name?: string
  message?: string
  content?: string
  created_at?: string
  read_at?: string | null
}
