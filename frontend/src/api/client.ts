import type {
  AgentOverview,
  BuiltinAgent,
  ChatAttachment,
  HeartbeatSettings,
  HeartbeatTask,
  McpServer,
  McpRegistryPage,
  McpRegistryServer,
  MetaAccount,
  MetaConnection,
  MetaOAuthStart,
  MetaPlatformDefinition,
  MetaWhatsAppConnection,
  MetaWhatsAppDefinition,
  ModelRecord,
  EmbeddingModelRecord,
  Notification,
  Profile,
  ProviderRecord,
  ServerToolsState,
  SkillAssignment,
  SkillRecord,
  SkillStorePage,
  SkillTarget,
  Supervisor,
  StoreSkill,
  SetupDescriptor,
  SetupActionResult,
  SpeechAdapterSpec,
  Subagent,
  SubagentNode,
  SubagentPlacement,
  TelegramSettings,
  TtsVoiceCatalog,
  VoiceSettings,
  VoiceModelRecord,
  WhatsAppSettings,
  Workflow,
  WorkflowNodePlacement,
} from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public data: Record<string, unknown> = {},
  ) {
    super(message)
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers:
      options.body instanceof FormData
        ? options.headers
        : { 'Content-Type': 'application/json', ...options.headers },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok)
    throw new ApiError(data.error || 'The request could not be completed.', response.status, data)
  return data as T
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const api = {
  chat: {
    uploadAttachment: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      return request<ChatAttachment>('/api/chat/attachments', { method: 'POST', body })
    },
  },
  profile: {
    get: () => request<Profile>('/api/profile'),
    update: (body: Partial<Profile>) => request<Profile>('/api/profile', json('PUT', body)),
  },
  providers: {
    list: () => request<ProviderRecord[]>('/api/providers'),
    create: (body: object) => request<ProviderRecord>('/api/providers', json('POST', body)),
    update: (id: number, body: object) =>
      request<ProviderRecord>(`/api/providers/${id}`, json('PUT', body)),
    remove: (id: number) => request(`/api/providers/${id}`, json('DELETE')),
  },
  overview: {
    get: () => request<AgentOverview>('/api/agent-overview'),
    updateSupervisor: (body: { model_id: number; skill_ids?: number[] }) =>
      request<Supervisor>('/api/supervisor', json('PUT', body)),
    updateBuiltin: (key: string, body: object) =>
      request(`/api/builtin-agents/${key}`, json('PUT', body)),
  },
  builtins: {
    list: () => request<BuiltinAgent[]>('/api/builtin-agents'),
    update: (key: string, body: object) =>
      request<BuiltinAgent>(`/api/builtin-agents/${key}`, json('PUT', body)),
    setupKnowledge: () =>
      request<BuiltinAgent>('/api/builtin-agents/knowledge/service/setup', json('POST')),
    testKnowledge: () =>
      request<BuiltinAgent>('/api/builtin-agents/knowledge/service/test', json('POST')),
  },
  models: {
    list: () => request<ModelRecord[]>('/api/models'),
    create: (body: object) => request<ModelRecord>('/api/models', json('POST', body)),
    update: (id: number, body: object) =>
      request<ModelRecord>(`/api/models/${id}`, json('PUT', body)),
    remove: (id: number) => request(`/api/models/${id}`, json('DELETE')),
  },
  modelCatalog: {
    list: () =>
      request<Array<ModelRecord | EmbeddingModelRecord | VoiceModelRecord>>('/api/model-catalog'),
  },
  embeddingModels: {
    list: () => request<EmbeddingModelRecord[]>('/api/embedding-models'),
    create: (body: object) =>
      request<EmbeddingModelRecord>('/api/embedding-models', json('POST', body)),
    update: (id: number, body: object) =>
      request<EmbeddingModelRecord>(`/api/embedding-models/${id}`, json('PUT', body)),
    remove: (id: number) => request(`/api/embedding-models/${id}`, json('DELETE')),
    test: (id: number) =>
      request<EmbeddingModelRecord>(`/api/embedding-models/${id}/test`, json('POST')),
    discover: (body: object) =>
      request<{ models: string[] }>('/api/embedding-models/discover', json('POST', body)),
  },
  voiceModels: {
    list: () => request<VoiceModelRecord[]>('/api/voice-models'),
    create: (body: object) => request<VoiceModelRecord>('/api/voice-models', json('POST', body)),
    update: (id: number, body: object) =>
      request<VoiceModelRecord>(`/api/voice-models/${id}`, json('PUT', body)),
    remove: (id: number) => request(`/api/voice-models/${id}`, json('DELETE')),
    adapters: (kind?: 'tts' | 'stt') =>
      request<SpeechAdapterSpec[]>(`/api/speech-adapters${kind ? `?kind=${kind}` : ''}`),
    discover: (body: object) =>
      request<{ target: 'models' | 'voices'; items: Array<{ id: string; label: string }> }>(
        '/api/voice-models/discover',
        json('POST', body),
      ),
    test: (id: number) =>
      request<{
        ok: boolean
        message: string
        mime_type?: string
        bytes?: number
        model: VoiceModelRecord
      }>(`/api/voice-models/${id}/test`, json('POST')),
  },
  servers: {
    list: () => request<McpServer[]>('/api/mcp-servers'),
    create: (body: object) => request<McpServer>('/api/mcp-servers', json('POST', body)),
    update: (id: number, body: object) =>
      request<McpServer>(`/api/mcp-servers/${id}`, json('PUT', body)),
    remove: (id: number) => request(`/api/mcp-servers/${id}`, json('DELETE')),
    tools: (id: number) => request<ServerToolsState>(`/api/mcp-servers/${id}/tools`),
    test: (id: number) => request<ServerToolsState>(`/api/mcp-servers/${id}/test`, json('POST')),
    setup: (id: number) => request<SetupDescriptor>(`/api/mcp-servers/${id}/setup`),
    setupAction: (id: number, action: string) =>
      request<SetupActionResult>(
        `/api/mcp-servers/${id}/setup/actions/${encodeURIComponent(action)}`,
        json('POST'),
      ),
    setupFile: (id: number, action: string, file: File) => {
      const body = new FormData()
      body.append('file', file)
      return request(`/api/mcp-servers/${id}/setup/files/${encodeURIComponent(action)}`, {
        method: 'POST',
        body,
      })
    },
  },
  mcpRegistry: {
    providers: () =>
      request<Array<{ id: string; name: string; url: string }>>('/api/mcp-registry/providers'),
    browse: (query = '', cursor = '') => {
      const params = new URLSearchParams()
      if (query) params.set('query', query)
      if (cursor) params.set('cursor', cursor)
      return request<McpRegistryPage>(`/api/mcp-registry?${params}`)
    },
    details: (reference: string, version = 'latest') => {
      const params = new URLSearchParams({ reference, version })
      return request<McpRegistryServer>(`/api/mcp-registry/details?${params}`)
    },
  },
  skills: {
    list: () => request<SkillRecord[]>('/api/skills'),
    get: (id: number) => request<SkillRecord>(`/api/skills/${id}`),
    targets: () => request<SkillTarget[]>('/api/skills/targets'),
    import: (files: File[], paths: string[]) => {
      const body = new FormData()
      files.forEach((file) => body.append('files', file))
      body.append('paths', JSON.stringify(paths))
      return request<SkillRecord>('/api/skills/import', { method: 'POST', body })
    },
    assign: (id: number, assignments: SkillAssignment[]) =>
      request<SkillRecord>(`/api/skills/${id}/assignments`, json('PUT', { assignments })),
    remove: (id: number) => request(`/api/skills/${id}`, json('DELETE')),
  },
  skillStore: {
    providers: () =>
      request<Array<{ id: string; name: string; supports_install: boolean }>>(
        '/api/skill-store/providers',
      ),
    browse: (provider: string, query = '', cursor = '') => {
      const params = new URLSearchParams({ provider })
      if (query) params.set('query', query)
      if (cursor) params.set('cursor', cursor)
      return request<SkillStorePage>(`/api/skill-store?${params}`)
    },
    details: (provider: string, reference: string) => {
      const params = new URLSearchParams({ provider, reference })
      return request<StoreSkill>(`/api/skill-store/details?${params}`)
    },
    install: (provider: string, reference: string, version = '') =>
      request<SkillRecord>(
        '/api/skill-store/install',
        json('POST', { provider, reference, version }),
      ),
  },
  agents: {
    list: () => request<Subagent[]>('/api/subagents'),
    create: (body: object) => request<Subagent>('/api/subagents', json('POST', body)),
    update: (id: number, body: object) =>
      request<Subagent>(`/api/subagents/${id}`, json('PUT', body)),
    remove: (id: number) => request(`/api/subagents/${id}`, json('DELETE')),
  },
  agentNodes: {
    list: (workflowId?: number) =>
      request<SubagentPlacement[]>(
        `/api/subagent-nodes${workflowId === undefined ? '' : `?workflow_id=${workflowId}`}`,
      ),
    create: (body: {
      subagent_id: number
      parent_node_id: number | null
      workflow_id?: number
      position?: number
    }) => request<SubagentNode>('/api/subagent-nodes', json('POST', body)),
    get: (id: number) => request<SubagentNode>(`/api/subagent-nodes/${id}`),
    update: (id: number, body: { enabled_tools: string[] | null }) =>
      request<SubagentNode>(`/api/subagent-nodes/${id}`, json('PUT', body)),
    remove: (id: number) =>
      request<{ ok: boolean; removed_nodes: number }>(`/api/subagent-nodes/${id}`, json('DELETE')),
  },
  workflows: {
    list: () => request<Workflow[]>('/api/workflows'),
    create: (body: object) => request<Workflow>('/api/workflows', json('POST', body)),
    update: (id: number, body: object) =>
      request<Workflow>(`/api/workflows/${id}`, json('PUT', body)),
    remove: (id: number) => request(`/api/workflows/${id}`, json('DELETE')),
  },
  workflowNodes: {
    list: (ownerWorkflowId?: number) =>
      request<WorkflowNodePlacement[]>(
        `/api/workflow-nodes${ownerWorkflowId === undefined ? '' : `?owner_workflow_id=${ownerWorkflowId}`}`,
      ),
    create: (body: {
      child_workflow_id: number
      parent_node_id: number | null
      owner_workflow_id?: number
      position?: number
    }) => request<WorkflowNodePlacement>('/api/workflow-nodes', json('POST', body)),
    remove: (id: number) => request(`/api/workflow-nodes/${id}`, json('DELETE')),
  },
  voice: {
    get: () => request<VoiceSettings>('/api/voice-settings'),
    update: (body: object) => request<VoiceSettings>('/api/voice-settings', json('PUT', body)),
    voices: (provider: string, model: string) => {
      const query = new URLSearchParams({ provider, model })
      return request<TtsVoiceCatalog>(`/api/tts-voices?${query}`)
    },
  },
  telegram: {
    get: () => request<TelegramSettings>('/api/telegram'),
    update: (body: object) => request<TelegramSettings>('/api/telegram', json('PUT', body)),
    test: () => request<TelegramSettings>('/api/telegram/test', json('POST')),
    pairingCode: () =>
      request<{ code: string; command?: string; expires_at?: string }>(
        '/api/telegram/pairing-code',
        json('POST'),
      ),
    disconnect: () => request<TelegramSettings>('/api/telegram/pairing', json('DELETE')),
    removeToken: () => request<TelegramSettings>('/api/telegram/token', json('DELETE')),
  },
  whatsapp: {
    get: () => request<WhatsAppSettings>('/api/whatsapp'),
    update: (body: object) => request<WhatsAppSettings>('/api/whatsapp', json('PUT', body)),
    test: () => request<WhatsAppSettings>('/api/whatsapp/test', json('POST')),
    pairingCode: () =>
      request<{ code: string; command?: string; expires_at?: string }>(
        '/api/whatsapp/pairing-code',
        json('POST'),
      ),
    disconnect: () => request<WhatsAppSettings>('/api/whatsapp/pairing', json('DELETE')),
    removeCredentials: () => request<WhatsAppSettings>('/api/whatsapp/credentials', json('DELETE')),
  },
  meta: {
    platforms: () => request<MetaPlatformDefinition[]>('/api/meta/platforms'),
    connections: (platform?: string) => {
      const query = platform ? `?${new URLSearchParams({ platform })}` : ''
      return request<MetaConnection[]>(`/api/meta/connections${query}`)
    },
    create: (body: object) => request<MetaConnection>('/api/meta/connections', json('POST', body)),
    update: (id: number, body: object) =>
      request<MetaConnection>(`/api/meta/connections/${id}`, json('PUT', body)),
    remove: (id: number) => request(`/api/meta/connections/${id}`, json('DELETE')),
    startOauth: (id: number) =>
      request<MetaOAuthStart>(`/api/meta/connections/${id}/oauth/start`, json('POST')),
    test: (id: number) => request<MetaConnection>(`/api/meta/connections/${id}/test`, json('POST')),
    updateAccount: (id: number, enabled: boolean) =>
      request<MetaAccount>(`/api/meta/accounts/${id}`, json('PATCH', { enabled })),
    whatsapp: {
      definition: () => request<MetaWhatsAppDefinition>('/api/meta/whatsapp/definition'),
      connections: () => request<MetaWhatsAppConnection[]>('/api/meta/whatsapp/connections'),
      create: (body: object) =>
        request<MetaWhatsAppConnection>('/api/meta/whatsapp/connections', json('POST', body)),
      update: (id: number, body: object) =>
        request<MetaWhatsAppConnection>(`/api/meta/whatsapp/connections/${id}`, json('PUT', body)),
      remove: (id: number) => request(`/api/meta/whatsapp/connections/${id}`, json('DELETE')),
      test: (id: number) =>
        request<MetaWhatsAppConnection>(`/api/meta/whatsapp/connections/${id}/test`, json('POST')),
    },
  },
  heartbeat: {
    get: () => request<HeartbeatSettings>('/api/heartbeat'),
    createTask: (body: object) =>
      request<HeartbeatTask>('/api/heartbeat/tasks', json('POST', body)),
    updateTask: (id: number, body: object) =>
      request<HeartbeatTask>(`/api/heartbeat/tasks/${id}`, json('PUT', body)),
    removeTask: (id: number) => request(`/api/heartbeat/tasks/${id}`, json('DELETE')),
    runTask: (id: number) => request<HeartbeatTask>(`/api/heartbeat/tasks/${id}/run`, json('POST')),
    notifications: (unreadOnly = false) =>
      request<{ notifications: Notification[] }>(
        `/api/heartbeat/notifications?unread_only=${unreadOnly}`,
      ),
    markNotificationRead: (id: number) =>
      request<{ ok: boolean }>(`/api/heartbeat/notifications/${id}/read`, json('PATCH')),
    deleteNotification: (id: number) =>
      request<{ ok: boolean }>(`/api/heartbeat/notifications/${id}`, json('DELETE')),
  },
}
