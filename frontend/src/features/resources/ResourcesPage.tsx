import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Bot,
  AudioLines,
  Boxes,
  Cable,
  ChevronLeft,
  Cpu,
  Edit3,
  Layers3,
  Mic2,
  Plus,
  Save,
  Search,
  Trash2,
  Type,
  Workflow,
  Wrench,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import type {
  BuiltinAgent,
  EmbeddingModelRecord,
  McpServer,
  ModelRecord,
  SkillRecord,
  Subagent,
  VoiceModelRecord,
} from '../../api/types'
import { McpIcon } from '../../components/icons/McpIcon'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Loading } from '../../components/ui/Loading'
import { Modal } from '../../components/ui/Modal'
import { Status } from '../../components/ui/Status'
import {
  useAgents,
  useBuiltins,
  useEmbeddingModels,
  useModelCatalog,
  useModels,
  useProviders,
  useServers,
  useSkills,
  keys,
} from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'
import { AgentForm } from './AgentForm'
import { BuiltinAgentDetails, type BuiltinAgentDetailsHandle } from './BuiltinAgentDetails'
import { EmbeddingModelDetails } from './EmbeddingModelDetails'
import { EmbeddingModelForm } from './EmbeddingModelForm'
import { ModelForm } from './ModelForm'
import { ResourceDetails } from './ResourceDetails'
import { ServerForm } from './ServerForm'
import { VoiceModelDetails } from './VoiceModelDetails'
import { VoiceModelForm } from './VoiceModelForm'

type Kind = 'models' | 'servers' | 'agents'
type Item =
  ModelRecord | EmbeddingModelRecord | VoiceModelRecord | McpServer | Subagent | BuiltinAgent
type ResourceMode = 'built-in' | 'dynamic'
type ModelType = 'all' | 'llm' | 'tts' | 'stt' | 'embedding'

const modelTypes: Array<{
  id: ModelType
  label: string
  description?: string
  icon?: typeof Type
}> = [
  { id: 'all', label: 'All' },
  {
    id: 'llm',
    label: 'Text',
    description: 'Chat, reasoning, and tool-calling models.',
    icon: Type,
  },
  {
    id: 'tts',
    label: 'Speech',
    description: 'Turn generated text into spoken audio.',
    icon: AudioLines,
  },
  {
    id: 'stt',
    label: 'Transcription',
    description: 'Turn recorded speech into text.',
    icon: Mic2,
  },
  {
    id: 'embedding',
    label: 'Embeddings',
    description: 'Generate vectors for search and knowledge.',
    icon: Boxes,
  },
]

function modelItemType(
  item: ModelRecord | EmbeddingModelRecord | VoiceModelRecord,
): Exclude<ModelType, 'all'> {
  if ('kind' in item) return item.kind
  if ('adapter' in item) return 'embedding'
  return 'llm'
}

const meta = {
  models: {
    title: 'Models',
    singular: 'model',
    description: 'Manage local and cloud models used by your agents',
  },
  servers: {
    title: 'MCP Servers',
    singular: 'MCP server',
    description: 'Manage services and tools connected through MCP',
  },
  agents: {
    title: 'Subagents',
    singular: 'subagent',
    description: 'Manage specialists the supervisor can delegate work to',
  },
}

function transportLabel(transport: McpServer['transport']) {
  if (transport === 'stdio') return 'Local process'
  if (transport === 'streamable_http') return 'Streamable HTTP'
  return 'Server-sent events'
}

function readableEmbeddingAdapter(adapter: EmbeddingModelRecord['adapter']) {
  return adapter === 'ollama' ? 'Ollama' : 'OpenAI-compatible'
}

export function ResourcesPage({ kind }: { kind: Kind }) {
  const client = useQueryClient()
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const models = useModels()
  const providers = useProviders()
  const modelCatalog = useModelCatalog()
  const embeddingModels = useEmbeddingModels()
  const servers = useServers()
  const skills = useSkills()
  const agents = useAgents()
  const builtins = useBuiltins()
  const resourceMode: ResourceMode = params.get('view') === 'built-in' ? 'built-in' : 'dynamic'
  const requestedModelType = params.get('type') as ModelType | null
  const modelType: ModelType = modelTypes.some((option) => option.id === requestedModelType)
    ? (requestedModelType as ModelType)
    : 'all'
  const hasTypePicker = kind === 'agents'
  const isBuiltins = kind === 'agents' && resourceMode === 'built-in'
  const query = isBuiltins
    ? builtins
    : kind === 'models'
      ? modelCatalog
      : kind === 'servers'
        ? servers
        : kind === 'agents'
          ? agents
          : models
  const modelData = useMemo(
    () =>
      ([...(modelCatalog.data || [])] as Item[]).sort((left, right) =>
        left.name.localeCompare(right.name),
      ),
    [modelCatalog.data],
  )
  const queryData = (kind === 'models' ? modelData : query.data || []) as Item[]
  const data = useMemo(
    () =>
      kind === 'models'
        ? queryData.filter(
            (item) =>
              modelType === 'all' ||
              modelItemType(item as ModelRecord | EmbeddingModelRecord | VoiceModelRecord) ===
                modelType,
          )
        : queryData,
    [kind, modelType, queryData],
  )
  const [selected, setSelected] = useState<Item | null>(null)
  const [editing, setEditing] = useState<Item | null | undefined>(undefined)
  const [deleting, setDeleting] = useState(false)
  const [search, setSearch] = useState('')
  const [createModelType, setCreateModelType] = useState<Exclude<ModelType, 'all'> | null>(null)
  const [modelCreateStage, setModelCreateStage] = useState<'closed' | 'type' | 'form'>('closed')
  const activeModelType: Exclude<ModelType, 'all'> =
    kind === 'models'
      ? modelCreateStage === 'form' && createModelType
        ? createModelType
        : editing === null
          ? createModelType || (modelType === 'all' ? 'llm' : modelType)
          : editing
            ? modelItemType(editing as ModelRecord | EmbeddingModelRecord | VoiceModelRecord)
            : selected
              ? modelItemType(selected as ModelRecord | EmbeddingModelRecord | VoiceModelRecord)
              : modelType === 'all'
                ? createModelType || 'llm'
                : modelType
      : 'llm'
  const isEmbeddingMode = kind === 'models' && activeModelType === 'embedding'
  const isVoiceModelMode =
    kind === 'models' && (activeModelType === 'tts' || activeModelType === 'stt')
  const builtinDetailsRef = useRef<BuiltinAgentDetailsHandle>(null)
  const formId = `${kind}-form`
  const info = meta[kind]
  const selectedAgentPlacements =
    kind === 'agents' && !isBuiltins ? ((selected as Subagent | null)?.placement_count ?? 0) : 0

  useEffect(() => {
    if ((location.state as { resetResourceList?: boolean } | null)?.resetResourceList) {
      setEditing(undefined)
      setSelected(null)
      setDeleting(false)
    }
  }, [location.key, location.state])

  useEffect(() => setSearch(''), [resourceMode, kind])

  useEffect(() => {
    if (isBuiltins) {
      const key = params.get('builtin')
      if (!key || !data.length) return
      const found = data.find((item) => (item as BuiltinAgent).key === key)
      if (found) setSelected(found)
      return
    }
    const id = Number(params.get('open'))
    if (!id || !data.length) return
    const openType = params.get('model') as Exclude<ModelType, 'all'> | null
    const found = data.find(
      (item) =>
        'id' in item &&
        item.id === id &&
        (kind !== 'models' ||
          !openType ||
          modelItemType(item as ModelRecord | EmbeddingModelRecord | VoiceModelRecord) ===
            openType),
    )
    if (found) setSelected(found)
  }, [params, data, isBuiltins, kind])

  const refresh = async () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys[kind] }),
      client.invalidateQueries({ queryKey: keys.embeddingModels }),
      client.invalidateQueries({ queryKey: keys.voiceModels }),
      client.invalidateQueries({ queryKey: keys.modelCatalog }),
      client.invalidateQueries({ queryKey: keys.voice }),
      client.invalidateQueries({ queryKey: keys.builtins }),
      client.invalidateQueries({ queryKey: keys.skills }),
      client.invalidateQueries({ queryKey: keys.overview }),
      client.invalidateQueries({ queryKey: keys.agentNodes }),
    ])

  const save = useMutation({
    mutationFn: async (body: object) => {
      if (isBuiltins) {
        if (!editing) throw new Error('No built-in subagent is selected.')
        return api.builtins.update((editing as BuiltinAgent).key, body)
      }
      if (isEmbeddingMode) {
        if (editing) {
          return api.embeddingModels.update((editing as EmbeddingModelRecord).id, body)
        }
        return api.embeddingModels.create(body)
      }
      if (isVoiceModelMode) {
        if (editing) return api.voiceModels.update((editing as VoiceModelRecord).id, body)
        return api.voiceModels.create(body)
      }
      if (editing) {
        return api[kind].update((editing as ModelRecord | McpServer | Subagent).id, body)
      }
      return api[kind].create(body)
    },
    onSuccess: async (saved) => {
      await refresh()
      if (kind === 'models') setModelCreateStage('closed')
      const item = saved as Item
      const savedModelType =
        kind === 'models'
          ? modelItemType(item as ModelRecord | EmbeddingModelRecord | VoiceModelRecord)
          : null
      setEditing(undefined)
      setSelected(item)
      setParams(
        isBuiltins
          ? { view: 'built-in', builtin: (item as BuiltinAgent).key }
          : kind === 'agents'
            ? { view: 'dynamic', open: String((item as Subagent).id) }
            : kind === 'servers'
              ? { view: resourceMode, open: String((item as McpServer).id) }
              : {
                  ...(modelType === 'all' ? {} : { type: savedModelType as string }),
                  model: savedModelType as string,
                  open: String((item as ModelRecord | EmbeddingModelRecord | VoiceModelRecord).id),
                },
      )
    },
  })
  const remove = useMutation({
    mutationFn: () => {
      if (!selected || isBuiltins) return Promise.resolve()
      if (isEmbeddingMode) {
        return api.embeddingModels.remove((selected as EmbeddingModelRecord).id)
      }
      if (isVoiceModelMode) return api.voiceModels.remove((selected as VoiceModelRecord).id)
      return api[kind].remove((selected as ModelRecord | McpServer | Subagent).id)
    },
    onSuccess: async () => {
      setParams(
        hasTypePicker
          ? { view: resourceMode }
          : kind === 'models' && modelType !== 'all'
            ? { type: modelType }
            : {},
      )
      await refresh()
      setSelected(null)
      setDeleting(false)
    },
  })
  const builtinConnect = useMutation<BuiltinAgent, Error, boolean>({
    mutationFn: (connected) => {
      if (!selected || !isBuiltins) throw new Error('No built-in subagent is selected.')
      return api.builtins.update((selected as BuiltinAgent).key, { connected })
    },
    onSuccess: async (saved) => {
      await refresh()
      setSelected(saved)
      setEditing((current) => (current && 'key' in current ? saved : current))
    },
  })
  const builtinConfig = useMutation<
    BuiltinAgent,
    Error,
    {
      model_id: number | null
      generation_model_id?: number | null
      automatic_knowledge_enabled?: boolean
      embedding_enabled?: boolean
      embedding_model_id?: number | null
      confirm_tools?: string[]
      skill_ids?: number[]
    }
  >({
    mutationFn: (body) => {
      if (!selected || !isBuiltins) throw new Error('No built-in subagent is selected.')
      return api.builtins.update((selected as BuiltinAgent).key, body)
    },
    onSuccess: async (saved) => {
      await refresh()
      setSelected(saved)
      setEditing(undefined)
    },
  })

  const rows = useMemo(
    () =>
      data.map((item) => {
        if (isBuiltins) {
          const builtin = item as BuiltinAgent
          return {
            title: builtin.name,
            subtitle: builtin.description,
            facts: [
              {
                value: builtin.model || 'Installation fallback',
                title: `Model: ${builtin.model || 'Installation fallback'}`,
                icon: Cpu,
              },
              {
                value: `${builtin.tools?.length || 0} built-in tool${builtin.tools?.length === 1 ? '' : 's'}`,
                title: `${builtin.tools?.length || 0} registered built-in tools`,
                icon: Wrench,
              },
            ],
            status: builtin.connected ? 'connected' : 'disconnected',
          }
        }
        const model = item as ModelRecord
        const embedding = item as EmbeddingModelRecord
        const voiceModel = item as VoiceModelRecord
        const server = item as McpServer
        const agent = item as Subagent
        const itemModelType =
          kind === 'models'
            ? modelItemType(item as ModelRecord | EmbeddingModelRecord | VoiceModelRecord)
            : null
        if (itemModelType === 'embedding')
          return {
            title: embedding.name,
            subtitle: '',
            facts: [
              {
                value: readableEmbeddingAdapter(embedding.adapter),
                title: `Connection: ${readableEmbeddingAdapter(embedding.adapter)}`,
                icon: Layers3,
              },
              {
                value: embedding.dimensions
                  ? `${embedding.model} · ${embedding.dimensions}d`
                  : embedding.model,
                title: `Model: ${embedding.model}`,
                icon: Cpu,
              },
            ],
            status: embedding.connection_status,
          }
        if (itemModelType === 'tts' || itemModelType === 'stt')
          return {
            title: voiceModel.name,
            subtitle: '',
            facts: [
              {
                value: itemModelType === 'tts' ? 'Speech' : 'Transcription',
                title: `Type: ${itemModelType === 'tts' ? 'Text-to-speech' : 'Speech-to-text'}`,
                icon: itemModelType === 'tts' ? AudioLines : Mic2,
              },
              {
                value: voiceModel.model,
                title: `Model: ${voiceModel.model}`,
                icon: Cpu,
              },
            ],
            status: undefined,
          }
        if (kind === 'models')
          return {
            title: model.name,
            subtitle: '',
            facts: [
              { value: model.provider, title: `Provider: ${model.provider}`, icon: Layers3 },
              { value: model.model, title: `Model: ${model.model}`, icon: Cpu },
            ],
            status: undefined,
          }
        if (kind === 'servers')
          return {
            title: server.name,
            subtitle: server.description,
            facts: [
              {
                value: transportLabel(server.transport),
                title: `Transport: ${transportLabel(server.transport)}`,
                icon: Cable,
              },
              {
                value:
                  server.tool_count === undefined ? 'Not loaded' : `${server.tool_count} available`,
                title:
                  server.tool_count === undefined
                    ? 'Tools: Not loaded'
                    : `Tools: ${server.tool_count} available`,
                icon: Wrench,
              },
            ],
            status: server.connection_status || 'untested',
          }
        return {
          title: agent.name,
          subtitle: agent.description,
          facts: [
            {
              value: agent.mcp_sources?.length
                ? `${agent.mcp_sources.length} MCP source${agent.mcp_sources.length === 1 ? '' : 's'}`
                : 'Prompt only',
              title: agent.mcp_sources?.length
                ? `MCP: ${agent.mcp_sources.map((source) => source.mcp_server_name).join(', ')}`
                : 'No external tools',
              icon: McpIcon,
            },
            {
              value: `${agent.placement_count || 0} placement${agent.placement_count === 1 ? '' : 's'}`,
              title: `${agent.placement_count || 0} workflow placement${agent.placement_count === 1 ? '' : 's'}`,
              icon: Workflow,
            },
          ],
          status: undefined,
        }
      }),
    [data, isBuiltins, kind],
  )
  const listed = useMemo(() => {
    const term = search.trim().toLocaleLowerCase()
    return data
      .map((item, index) => ({ item, row: rows[index] }))
      .filter(
        ({ row }) =>
          !term ||
          [
            row.title,
            row.subtitle,
            ...row.facts.flatMap((fact) => [fact.value, fact.title]),
            row.status,
          ].some((value) =>
            String(value || '')
              .toLocaleLowerCase()
              .includes(term),
          ),
      )
  }, [data, rows, search])

  const openList = () => {
    setEditing(undefined)
    setSelected(null)
    setParams(
      hasTypePicker
        ? { view: resourceMode }
        : kind === 'models' && modelType !== 'all'
          ? { type: modelType }
          : {},
    )
  }
  const openCreate = () => {
    setSelected(null)
    save.reset()
    if (kind === 'models') {
      setCreateModelType(modelType === 'all' ? null : modelType)
      setModelCreateStage('type')
      return
    }
    setEditing(null)
    setParams(hasTypePicker ? { view: 'dynamic' } : {})
  }
  const chooseModelType = (next: Exclude<ModelType, 'all'>) => {
    setCreateModelType(next)
    setModelCreateStage('form')
    save.reset()
  }
  const closeModelCreate = () => {
    setModelCreateStage('closed')
    setCreateModelType(null)
    save.reset()
  }
  const cancelForm = () => {
    save.reset()
    setEditing(undefined)
    if (!selected)
      setParams(
        hasTypePicker
          ? { view: resourceMode }
          : kind === 'models' && modelType !== 'all'
            ? { type: modelType }
            : {},
      )
  }
  const submit = async (body: object) => {
    await save.mutateAsync(
      kind === 'agents' && !isBuiltins && !editing ? { ...body, connect_to_workflow: false } : body,
    )
  }

  const switchResourceMode = (mode: ResourceMode) => {
    if (mode === resourceMode) return
    setEditing(undefined)
    setSelected(null)
    setDeleting(false)
    setSearch('')
    setParams({ view: mode })
  }

  const switchModelType = (next: ModelType) => {
    if (next === modelType) return
    setEditing(undefined)
    setSelected(null)
    setDeleting(false)
    setParams(next === 'all' ? {} : { type: next })
  }

  const singular = isBuiltins
    ? 'built-in subagent'
    : isEmbeddingMode
      ? 'embedding model'
      : isVoiceModelMode
        ? `${activeModelType === 'tts' ? 'speech' : 'transcription'} model`
        : info.singular
  const collectionTitle = info.title

  const isModelEdit = kind === 'models' && Boolean(editing)
  const isAgentWrite = kind === 'agents' && !isBuiltins && editing !== undefined
  const isBuiltinAgentEdit = isBuiltins && Boolean(editing)
  const isServerWrite = kind === 'servers' && editing !== undefined
  const headerTitle = selected ? selected.name : info.title
  const headerDescription = selected ? `Saved ${singular} configuration` : info.description

  const headerActions = selected ? (
    <div className="resource-header-actions">
      {isBuiltins && (
        <Button
          className="resource-header-action"
          variant="primary"
          icon={<Edit3 size={15} />}
          onClick={() => {
            builtinConfig.reset()
            setEditing(selected)
          }}
          aria-label="Edit built-in subagent"
          title="Edit built-in subagent"
        />
      )}
      {!isBuiltins && (
        <Button
          className="resource-header-action"
          icon={<Trash2 size={15} />}
          onClick={() => {
            remove.reset()
            setDeleting(true)
          }}
          aria-label={`Delete ${singular}`}
          title={`Delete ${singular}`}
        />
      )}
      {!isBuiltins && (
        <Button
          className="resource-header-action"
          variant="primary"
          icon={<Edit3 size={15} />}
          onClick={() => {
            save.reset()
            setEditing(selected)
          }}
          aria-label={`Edit ${singular}`}
          title={`Edit ${singular}`}
        />
      )}
    </div>
  ) : !isBuiltins ? (
    <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
      Add {singular}
    </Button>
  ) : null

  return (
    <>
      <PageHeader
        title={headerTitle}
        description={headerDescription}
        leading={
          selected ? (
            <Button
              icon={<ChevronLeft size={15} />}
              onClick={openList}
              aria-label={`Back to ${collectionTitle.toLowerCase()}`}
              title={`Back to ${collectionTitle.toLowerCase()}`}
            />
          ) : undefined
        }
        actions={headerActions}
      />
      <div className={`page-content ${hasTypePicker || kind === 'models' ? 'stack' : ''}`}>
        {hasTypePicker && !selected && (
          <div className="model-location-picker" role="tablist" aria-label="Subagent type">
            <span
              className={`model-location-picker__indicator ${resourceMode === 'built-in' ? 'is-local' : ''}`}
              aria-hidden="true"
            />
            <button
              type="button"
              role="tab"
              className={resourceMode === 'dynamic' ? 'is-active' : ''}
              aria-selected={resourceMode === 'dynamic'}
              onClick={() => switchResourceMode('dynamic')}
            >
              Dynamic
            </button>
            <button
              type="button"
              role="tab"
              className={resourceMode === 'built-in' ? 'is-active' : ''}
              aria-selected={resourceMode === 'built-in'}
              onClick={() => switchResourceMode('built-in')}
            >
              Built-in
            </button>
          </div>
        )}
        {selected ? (
          <section className="resource-detail-page">
            {isBuiltins ? (
              <BuiltinAgentDetails
                key={(selected as BuiltinAgent).key}
                readOnly
                item={selected as BuiltinAgent}
                models={models.data || []}
                embeddingModels={embeddingModels.data || []}
                skills={(skills.data || []) as SkillRecord[]}
                skillsLoading={skills.isLoading}
                skillsError={skills.error instanceof Error ? skills.error.message : ''}
                connecting={builtinConnect.isPending}
                error={builtinConnect.error instanceof Error ? builtinConnect.error.message : ''}
                onConnect={(connected) => builtinConnect.mutate(connected)}
              />
            ) : isEmbeddingMode ? (
              <EmbeddingModelDetails
                item={selected as EmbeddingModelRecord}
                onTested={(saved) => setSelected(saved)}
              />
            ) : isVoiceModelMode ? (
              <VoiceModelDetails item={selected as VoiceModelRecord} />
            ) : (
              <ResourceDetails
                kind={kind}
                item={selected as ModelRecord | McpServer | Subagent}
                models={models.data || []}
                skills={(skills.data || []) as SkillRecord[]}
              />
            )}
          </section>
        ) : query.isLoading ? (
          <Loading />
        ) : query.error ? (
          <Feedback
            message={
              query.error instanceof Error ? query.error.message : 'Models could not be loaded.'
            }
          />
        ) : !data.length && kind !== 'models' ? (
          <div className="card empty-state">No {collectionTitle.toLowerCase()} saved yet.</div>
        ) : (
          <div className="resource-browser">
            <label className="resource-search">
              <Search size={13} />
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder={`Search ${collectionTitle.toLowerCase()}…`}
                aria-label={`Search ${collectionTitle.toLowerCase()}`}
              />
            </label>
            {kind === 'models' && (
              <div className="model-type-filters" role="group" aria-label="Filter models by type">
                {modelTypes.map((option) => {
                  const Icon = option.icon
                  return (
                    <button
                      type="button"
                      key={option.id}
                      className={`model-type-filter model-type-filter--${option.id} ${modelType === option.id ? 'is-active' : ''}`}
                      aria-pressed={modelType === option.id}
                      onClick={() => switchModelType(option.id)}
                    >
                      {Icon && <Icon size={13} aria-hidden="true" />}
                      {option.label}
                    </button>
                  )
                })}
              </div>
            )}
            {listed.length ? (
              <div className="resource-list">
                {listed.map(({ item, row }) => {
                  const isAgent = kind === 'agents'
                  const itemModelType =
                    kind === 'models'
                      ? modelItemType(item as ModelRecord | EmbeddingModelRecord | VoiceModelRecord)
                      : null
                  const itemModelTypeOption = itemModelType
                    ? modelTypes.find((option) => option.id === itemModelType)
                    : null
                  const ModelTypeIcon = itemModelTypeOption?.icon
                  return (
                    <button
                      className={`resource-row resource-row--${kind} ${row.status ? '' : 'resource-row--without-status'}`}
                      type="button"
                      key={
                        'key' in item
                          ? item.key
                          : kind === 'models'
                            ? `${modelItemType(item as ModelRecord | EmbeddingModelRecord | VoiceModelRecord)}:${item.id}`
                            : item.id
                      }
                      onClick={() => {
                        setEditing(undefined)
                        setSelected(item)
                        setParams(
                          isBuiltins
                            ? { view: 'built-in', builtin: (item as BuiltinAgent).key }
                            : {
                                ...(hasTypePicker ? { view: resourceMode } : {}),
                                ...(kind === 'models' && modelType !== 'all'
                                  ? { type: modelType }
                                  : {}),
                                ...(kind === 'models'
                                  ? {
                                      model: modelItemType(
                                        item as
                                          ModelRecord | EmbeddingModelRecord | VoiceModelRecord,
                                      ),
                                    }
                                  : {}),
                                open: String(
                                  (
                                    item as
                                      | ModelRecord
                                      | EmbeddingModelRecord
                                      | VoiceModelRecord
                                      | McpServer
                                      | Subagent
                                  ).id,
                                ),
                              },
                        )
                      }}
                    >
                      <div className="resource-row__identity">
                        {isAgent && (
                          <span className="avatar">
                            {(item as Subagent).has_icon ? (
                              <img src={`/api/subagents/${(item as Subagent).id}/icon`} alt="" />
                            ) : (
                              <Bot size={17} />
                            )}
                          </span>
                        )}
                        <span className="resource-row__identity-copy">
                          <span className="resource-row__title">
                            <strong>{row.title}</strong>
                            {itemModelType && itemModelTypeOption && ModelTypeIcon && (
                              <span
                                className={`model-card-type-badge model-card-type-badge--${itemModelType}`}
                                title={`${itemModelTypeOption.label} model`}
                                aria-label={`${itemModelTypeOption.label} model`}
                              >
                                <ModelTypeIcon size={13} aria-hidden="true" />
                              </span>
                            )}
                          </span>
                          {row.subtitle && <small>{row.subtitle}</small>}
                        </span>
                      </div>
                      {row.status && <Status value={row.status} />}
                      <span className="resource-row__facts">
                        {row.facts.map((fact) => {
                          const FactIcon = fact.icon
                          return (
                            <span key={fact.title} title={fact.title} aria-label={fact.title}>
                              <FactIcon size={12} aria-hidden="true" /> {fact.value}
                            </span>
                          )
                        })}
                      </span>
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="card empty-state resource-search-empty">
                {search.trim()
                  ? `No ${collectionTitle.toLowerCase()} match “${search.trim()}”.`
                  : kind === 'models' && modelType !== 'all'
                    ? `No ${modelTypes.find((option) => option.id === modelType)?.label.toLowerCase()} models saved yet.`
                    : `No ${collectionTitle.toLowerCase()} saved yet.`}
              </div>
            )}
          </div>
        )}
      </div>
      <Modal
        open={isAgentWrite}
        wide
        integrated
        className="modal--compact-write-form modal--subagent-write-form"
        title={editing ? `Edit ${editing.name}` : 'Add subagent'}
        description={
          editing
            ? 'Update its role, skills, tools, and security.'
            : 'Configure its role, skills, tools, and security.'
        }
        headingActions={
          editing ? (
            <Button
              className="modal-heading-save-button"
              variant="primary"
              type="submit"
              form={formId}
              icon={<Save size={15} />}
              busy={save.isPending}
              aria-label="Save subagent changes"
              title="Save changes"
            />
          ) : undefined
        }
        onClose={cancelForm}
      >
        <div className="compact-write-modal-form subagent-write-modal-form">
          <AgentForm
            key={(editing as Subagent | undefined)?.id || 'new-agent'}
            item={(editing as Subagent | undefined) || undefined}
            models={models.data || []}
            servers={servers.data || []}
            formId={formId}
            busy={save.isPending}
            onSubmit={submit}
          />
        </div>
      </Modal>
      <Modal
        open={isServerWrite}
        wide
        integrated
        className="modal--compact-write-form modal--mcp-write-form"
        title={editing ? `Edit ${editing.name}` : 'Add MCP server'}
        description={
          editing
            ? 'Update this server connection and its private configuration.'
            : 'Configure a reusable MCP server connection.'
        }
        onClose={cancelForm}
      >
        <div className="compact-write-modal-form">
          <ServerForm
            key={(editing as McpServer | undefined)?.id || 'new-server'}
            item={(editing as McpServer | undefined) || undefined}
            formId={formId}
            onSubmit={submit}
          />
        </div>
        <div className="compact-form-actions">
          <Button variant="primary" type="submit" form={formId} busy={save.isPending}>
            {editing ? 'Save changes' : 'Add server'}
          </Button>
        </div>
      </Modal>
      <Modal
        open={isBuiltinAgentEdit}
        wide
        integrated
        className="modal--compact-write-form modal--subagent-write-form modal--builtin-agent-write-form"
        title={`Edit ${editing?.name || 'built-in subagent'}`}
        description="Update this built-in specialist's models, skills, and security."
        headingActions={
          <Button
            className="modal-heading-save-button"
            variant="primary"
            type="button"
            icon={<Save size={15} />}
            busy={builtinConfig.isPending}
            onClick={() => builtinDetailsRef.current?.save()}
            aria-label="Save built-in subagent changes"
            title="Save changes"
          />
        }
        onClose={cancelForm}
      >
        {editing && (
          <div className="compact-write-modal-form">
            <BuiltinAgentDetails
              key={`${(editing as BuiltinAgent).key}:edit`}
              ref={builtinDetailsRef}
              compact
              item={editing as BuiltinAgent}
              models={models.data || []}
              embeddingModels={embeddingModels.data || []}
              skills={(skills.data || []) as SkillRecord[]}
              skillsLoading={skills.isLoading}
              skillsError={skills.error instanceof Error ? skills.error.message : ''}
              saving={builtinConfig.isPending}
              connecting={builtinConnect.isPending}
              error={
                builtinConfig.error instanceof Error
                  ? builtinConfig.error.message
                  : builtinConnect.error instanceof Error
                    ? builtinConnect.error.message
                    : ''
              }
              onConnect={(connected) => builtinConnect.mutate(connected)}
              onSave={(body) => builtinConfig.mutateAsync(body)}
            />
          </div>
        )}
      </Modal>
      <Modal
        open={isModelEdit}
        wide
        integrated
        className="modal--compact-write-form modal--model-write-form"
        title={`Edit ${editing?.name || 'model'}`}
        description="Update this model connection. Its model type cannot be changed."
        onClose={cancelForm}
      >
        <div className="compact-write-modal-form model-write-modal-form">
          {activeModelType === 'llm' && (
            <ModelForm
              item={editing as ModelRecord | undefined}
              providers={providers.data || []}
              formId={formId}
              onSubmit={submit}
            />
          )}
          {activeModelType === 'embedding' && (
            <EmbeddingModelForm
              item={editing as EmbeddingModelRecord | undefined}
              providers={providers.data || []}
              formId={formId}
              onSubmit={submit}
            />
          )}
          {(activeModelType === 'tts' || activeModelType === 'stt') && (
            <VoiceModelForm
              key={activeModelType}
              kind={activeModelType}
              item={editing as VoiceModelRecord | undefined}
              providers={providers.data || []}
              formId={formId}
              onSubmit={submit}
            />
          )}
        </div>
        <div className="compact-form-actions">
          <Button variant="primary" type="submit" form={formId} busy={save.isPending}>
            Save changes
          </Button>
        </div>
      </Modal>
      <Modal
        open={kind === 'models' && modelCreateStage !== 'closed'}
        wide={modelCreateStage === 'form'}
        integrated={modelCreateStage === 'form'}
        className={
          modelCreateStage === 'type'
            ? 'modal--model-choice'
            : 'modal--compact-write-form modal--model-write-form'
        }
        title={
          modelCreateStage === 'type'
            ? 'Add model'
            : `Add ${modelTypes.find((option) => option.id === createModelType)?.label.toLowerCase() || ''} model`
        }
        description={
          modelCreateStage === 'type'
            ? 'Choose the kind of model you want to configure.'
            : 'Configure this model connection. Its type cannot be changed after creation.'
        }
        onClose={closeModelCreate}
      >
        {modelCreateStage === 'type' ? (
          <div className="subagent-start model-create-chooser">
            <div className="subagent-start__choices model-create-choices">
              {modelTypes.slice(1).map((option) => {
                const Icon = option.icon
                return (
                  <button
                    key={option.id}
                    type="button"
                    className={`subagent-start__choice model-create-choice model-create-choice--${option.id} ${createModelType === option.id ? 'is-selected' : ''}`}
                    aria-pressed={createModelType === option.id}
                    onClick={() => chooseModelType(option.id as Exclude<ModelType, 'all'>)}
                  >
                    <span className="subagent-start__choice-title">
                      {Icon && <Icon size={18} aria-hidden="true" />}
                      <strong>{option.label}</strong>
                    </span>
                    <small>{option.description}</small>
                  </button>
                )
              })}
            </div>
          </div>
        ) : createModelType ? (
          <>
            <div key={`model-create-form:${createModelType}`} className="model-write-modal-form">
              {createModelType === 'llm' && (
                <ModelForm providers={providers.data || []} formId={formId} onSubmit={submit} />
              )}
              {createModelType === 'embedding' && (
                <EmbeddingModelForm
                  providers={providers.data || []}
                  formId={formId}
                  onSubmit={submit}
                />
              )}
              {(createModelType === 'tts' || createModelType === 'stt') && (
                <VoiceModelForm
                  key={createModelType}
                  kind={createModelType}
                  providers={providers.data || []}
                  formId={formId}
                  onSubmit={submit}
                />
              )}
            </div>
            <div className="compact-form-actions">
              <Button variant="primary" type="submit" form={formId} busy={save.isPending}>
                Create model
              </Button>
            </div>
          </>
        ) : null}
      </Modal>
      <ConfirmDialog
        open={deleting}
        title={`Delete ${singular}?`}
        message={
          kind === 'agents'
            ? `Permanently delete “${selected?.name || ''}”? Its ${selectedAgentPlacements} workflow placement${selectedAgentPlacements === 1 ? '' : 's'}, including nested branches, will be disconnected. Other saved subagents will remain available.`
            : `This permanently removes “${selected?.name || ''}”. Records in use cannot be deleted.`
        }
        confirmLabel="Delete"
        danger
        busy={remove.isPending}
        error={remove.error instanceof Error ? remove.error.message : ''}
        onConfirm={() => remove.mutate()}
        onCancel={() => {
          remove.reset()
          setDeleting(false)
        }}
      />
    </>
  )
}
