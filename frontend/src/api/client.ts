import type {
  AgentOverview,
  BuiltinAgent,
  ChatAttachment,
  HeartbeatSettings,
  HeartbeatTask,
  McpServer,
  ModelRecord,
  EmbeddingModelRecord,
  Notification,
  Profile,
  ServerToolsState,
  SetupDescriptor,
  SetupActionResult,
  Subagent,
  SubagentNode,
  SubagentPlacement,
  TelegramSettings,
  TtsVoiceCatalog,
  VoiceSettings,
  WhatsAppSettings,
  Workflow,
  WorkflowNodePlacement,
} from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
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
    throw new ApiError(data.error || 'The request could not be completed.', response.status)
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
  overview: {
    get: () => request<AgentOverview>('/api/agent-overview'),
    updateSupervisor: (model_id: number) => request('/api/supervisor', json('PUT', { model_id })),
    updateBuiltin: (key: string, body: object) =>
      request(`/api/builtin-agents/${key}`, json('PUT', body)),
  },
  builtins: {
    list: () => request<BuiltinAgent[]>('/api/builtin-agents'),
    update: (key: string, body: object) =>
      request<BuiltinAgent>(`/api/builtin-agents/${key}`, json('PUT', body)),
  },
  models: {
    list: () => request<ModelRecord[]>('/api/models'),
    create: (body: object) => request<ModelRecord>('/api/models', json('POST', body)),
    update: (id: number, body: object) =>
      request<ModelRecord>(`/api/models/${id}`, json('PUT', body)),
    remove: (id: number) => request(`/api/models/${id}`, json('DELETE')),
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
  heartbeat: {
    get: () => request<HeartbeatSettings>('/api/heartbeat'),
    update: (body: object) => request<HeartbeatSettings>('/api/heartbeat', json('PUT', body)),
    run: () => request<HeartbeatSettings>('/api/heartbeat/run', json('POST')),
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
