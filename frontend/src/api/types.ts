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
  location: 'cloud' | 'local'
  model: string
  provider: string
  provider_id?: Id | null
  provider_name?: string
  provider_base_url_id?: Id | null
  provider_base_url_name?: string
  provider_api_key_id?: Id | null
  provider_api_key_name?: string
  base_url: string
  api_key?: string
  api_key_configured?: boolean
}

export interface ProviderBaseUrl {
  id?: Id
  name: string
  value: string
}

export interface ProviderApiKey {
  id?: Id
  name: string
  value: string
  configured?: boolean
  preview?: string
}

export interface ProviderRecord {
  id: Id
  name: string
  description: string
  headers: Record<string, string>
  base_urls: ProviderBaseUrl[]
  api_keys: ProviderApiKey[]
  model_count: number
  created_at?: string
  updated_at?: string
}

export interface EmbeddingModelRecord {
  id: Id
  name: string
  location: 'cloud' | 'local'
  adapter: 'openai_compatible' | 'ollama'
  model: string
  base_url: string
  api_key?: string
  api_key_configured?: boolean
  provider_id?: Id | null
  provider_name?: string
  provider_base_url_id?: Id | null
  provider_base_url_name?: string
  provider_api_key_id?: Id | null
  provider_api_key_name?: string
  dimensions?: number | null
  connection_status: 'untested' | 'connected' | 'stale' | 'failed'
  last_tested_at?: string | null
  last_error?: string
}

export interface VoiceModelRecord {
  id: Id
  name: string
  kind: 'tts' | 'stt'
  location: 'cloud' | 'local'
  provider: 'piper' | 'moss_onnx' | 'openai_compatible' | 'google' | 'local_whisper' | string
  adapter: string
  model: string
  voice?: string
  base_url: string
  language: string
  provider_options: Record<string, string | number | boolean>
  connection_status: 'untested' | 'connected' | 'stale' | 'failed'
  last_tested_at?: string | null
  last_error?: string
  api_key?: string
  api_key_configured?: boolean
  provider_id?: Id | null
  provider_name?: string
  provider_base_url_id?: Id | null
  provider_base_url_name?: string
  provider_api_key_id?: Id | null
  provider_api_key_name?: string
}

export interface SpeechAdapterOption {
  key: string
  label: string
  type: 'text' | 'number' | 'integer' | 'boolean'
  default: string | number | boolean
  hint: string
  choices: Array<{ value: string; label: string }>
  advanced: boolean
}

export interface SpeechAdapterSpec {
  id: string
  kind: 'tts' | 'stt'
  label: string
  description: string
  locations: Array<'cloud' | 'local'>
  connection: 'http' | 'tcp' | 'aws' | 'none'
  transport: string
  modes: string[]
  model_label: string
  model_required: boolean
  voice: 'required' | 'optional' | 'none'
  language: 'required' | 'optional' | 'none'
  discovery: Array<'models' | 'voices'>
  options: SpeechAdapterOption[]
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
  source_type?: 'manual' | 'registry'
  source_name?: string
  source_ref?: string
  source_version?: string
  source_url?: string
}

export interface McpRegistryInstallOption {
  id: string
  kind: 'remote' | 'package'
  label: string
  transport: McpServer['transport']
  connection: string
  headers: Record<string, string>
  env: Record<string, string>
  auth_scheme: string
  requirements: string[]
}

export interface McpRegistryPublishedOption {
  id: string
  kind: 'remote' | 'package'
  label: string
  transport: string
  address: string
  registry: string
  version: string
  runtime: string
  requirements: string[]
  integrity_available: boolean
  configurable: boolean
}

export interface McpRegistryServer {
  provider: string
  provider_name: string
  reference: string
  name: string
  description: string
  version: string
  repository_url: string
  repository_source: string
  repository_subfolder: string
  website_url: string
  status: string
  status_message: string
  status_changed_at?: string | null
  published_at?: string | null
  updated_at?: string | null
  is_latest: boolean | null
  publisher_contact: string
  install_options: McpRegistryInstallOption[]
  published_options: McpRegistryPublishedOption[]
}

export interface McpRegistryPage {
  provider: string
  provider_name: string
  items: McpRegistryServer[]
  next_cursor: string
}

export interface McpCredentialFile {
  env_var: string
  filename: string
}

export interface SubagentDeveloperDefaults {
  max_tool_rounds: number
  tool_timeout_seconds: number
  task_timeout_seconds: number
}

export interface Subagent {
  id: Id
  name: string
  description: string
  system_prompt: string
  model_id: Id
  mcp_server_id: Id | null
  mcp_sources: SubagentMcpSource[]
  skill_ids: Id[]
  mcp_server_count?: number
  model?: string
  model_name?: string
  mcp_server_name?: string
  confirm_tool_calls: boolean
  confirm_tools: string[] | string
  dedupe_tools: string[] | string
  max_tool_rounds: number
  tool_timeout_seconds: number
  task_timeout_seconds: number
  developer_defaults?: SubagentDeveloperDefaults
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
  max_tool_rounds: number
  default_max_tool_rounds: number
  knowledge_service_status?: string | null
  knowledge_service_last_tested_at?: string | null
  knowledge_service_last_error?: string
  knowledge_protocol?: string | null
  knowledge_protocol_compatible?: boolean | null
  knowledge_protocol_missing_tools?: string[]
  computer_diagnostics?: Record<string, unknown>
  computer_backend?: string | null
  computer_backend_reason?: string
  automatic_knowledge_enabled?: boolean | null
  automatic_knowledge_available?: boolean | null
  embedding_enabled?: boolean | null
  embedding_model_id?: Id | null
  embedding_model_options?: Array<{
    id: Id
    label: string
    status: EmbeddingModelRecord['connection_status']
    dimensions?: number | null
  }>
  provider?: string
  enabled: boolean
  connected: boolean
  confirm_tools?: string[] | string
  tools?: ToolInfo[]
  skill_ids: Id[]
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
  skill_ids: Id[]
  model_options?: Array<{ id: Id; model: string; label: string }>
}

export type SkillAgentType = 'supervisor' | 'builtin' | 'subagent'

export interface SkillAssignment {
  agent_type: SkillAgentType
  agent_key: string
}

export interface SkillRecord {
  id: Id
  name: string
  description: string
  skill_md: string
  files: Array<{ path: string; size: number }>
  metadata: Record<string, unknown>
  source_type: string
  source_name: string
  source_ref: string
  source_url: string
  version: string
  assignments: SkillAssignment[]
  assignment_count: number
  file_count: number
  has_supporting_files: boolean
  created_at: string
  updated_at: string
}

export interface SkillTarget extends SkillAssignment {
  name: string
  group: string
}

export interface StoreSkill {
  provider: string
  provider_name: string
  slug: string
  reference: string
  name: string
  description: string
  version: string
  owner: string
  downloads: number
  stars: number
  installs: number
  versions: number
  comments: number
  bookmarks: number
  rolling_installs: number
  topics: string[]
  categories: string[]
  official: boolean
  installability: string
  visibility: string
  created_at?: number | string | null
  updated_at?: number | string | null
  changelog: string
  license: string
  skill_md: string
  permissions: Record<string, unknown>
  dependencies: Record<string, string>
  scan_findings: Array<{
    stage?: string
    severity?: string
    type?: string
    description?: string
    location?: string | null
  }>
  source_url: string
}

export interface SkillStorePage {
  provider: string
  items: StoreSkill[]
  next_cursor: string
}

export interface AgentOverview {
  supervisor: Supervisor
  builtins: BuiltinAgent[]
}

export interface VoiceProvider {
  model_id: Id | null
  name?: string
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

export type MetaPlatformId = 'facebook' | 'messenger' | 'instagram' | 'threads'

export interface MetaAuthStrategy {
  id: string
  label: string
}

export interface MetaCapability {
  id: string
  label: string
  description: string
  required?: boolean
  available?: boolean
  scopes?: string[]
  scopes_by_auth?: Record<string, string[]>
}

export interface MetaPlatformDefinition {
  id: MetaPlatformId
  label: string
  description: string
  account_kind: string
  default_api_version: string
  auth_strategies: MetaAuthStrategy[]
  excluded: string[]
  capabilities: MetaCapability[]
}

export interface MetaAccount {
  id: Id
  connection_id: Id
  external_id: string
  name: string
  username: string
  account_type: string
  enabled: boolean
  tasks: string[]
  capabilities: string[]
  metadata: Record<string, unknown>
  token_configured: boolean
}

export interface MetaConnection {
  id: Id
  platform: MetaPlatformId
  name: string
  auth_strategy: string
  enabled: boolean
  app_id: string
  api_version: string
  redirect_uri: string
  requested_capabilities: string[]
  token_type: string
  token_expires_at?: string | null
  connection_status: string
  last_error: string
  last_tested_at?: string | null
  app_secret_configured: boolean
  token_configured: boolean
  credentials_configured: boolean
  accounts: MetaAccount[]
}

export interface MetaOAuthStart {
  authorization_url: string
  redirect_uri: string
  expires_in: number
}

export interface MetaWhatsAppConnection {
  id: Id
  name: string
  enabled: boolean
  app_id: string
  phone_number_id: string
  business_account_id: string
  api_version: string
  display_phone_number: string
  verified_name: string
  connection_status: string
  last_error: string
  last_tested_at?: string | null
  token_configured: boolean
  app_secret_configured: boolean
  credentials_configured: boolean
  webhook_verified: boolean
  verify_token: string
  webhook_path: string
  requested_capabilities: string[]
  granted_permissions: string[]
  permissions_checked_at?: string | null
}

export interface MetaWhatsAppPermission {
  id: string
  label: string
  description: string
  required: boolean
}

export interface MetaWhatsAppCapability {
  id: string
  label: string
  description: string
  required?: boolean
  available?: boolean
  permissions: string[]
}

export interface MetaWhatsAppDefinition {
  id: 'whatsapp'
  label: string
  description: string
  account_kind: string
  default_api_version: string
  permissions: MetaWhatsAppPermission[]
  capabilities: MetaWhatsAppCapability[]
  excluded: string[]
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
  capabilities: HeartbeatCapability[]
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
