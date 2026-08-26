import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Bot,
  Cable,
  ChevronLeft,
  Cpu,
  Edit3,
  Layers3,
  Plus,
  Save,
  Search,
  Trash2,
  Workflow,
  Wrench,
  X,
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
} from '../../api/types'
import { McpIcon } from '../../components/icons/McpIcon'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Loading } from '../../components/ui/Loading'
import { Status } from '../../components/ui/Status'
import {
  useAgents,
  useBuiltins,
  useEmbeddingModels,
  useModels,
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

type Kind = 'models' | 'servers' | 'agents'
type Item = ModelRecord | EmbeddingModelRecord | McpServer | Subagent | BuiltinAgent
type ResourceMode = 'built-in' | 'dynamic'
type ModelMode = 'language' | 'embedding'

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
  const embeddingModels = useEmbeddingModels()
  const servers = useServers()
  const skills = useSkills()
  const agents = useAgents()
  const builtins = useBuiltins()
  const resourceMode: ResourceMode = params.get('view') === 'built-in' ? 'built-in' : 'dynamic'
  const modelMode: ModelMode = params.get('view') === 'embedding' ? 'embedding' : 'language'
  const hasTypePicker = kind === 'agents' || kind === 'servers'
  const isEmbeddingMode = kind === 'models' && modelMode === 'embedding'
  const isBuiltins = kind === 'agents' && resourceMode === 'built-in'
  const isBuiltinServerMode = kind === 'servers' && resourceMode === 'built-in'
  const query = isBuiltins
    ? builtins
    : isEmbeddingMode
      ? embeddingModels
      : kind === 'models'
        ? models
        : kind === 'servers'
          ? servers
          : agents
  const queryData = (query.data || []) as Item[]
  const data = useMemo(
    () =>
      kind === 'servers'
        ? queryData.filter((item) => Boolean((item as McpServer).managed) === isBuiltinServerMode)
        : queryData,
    [isBuiltinServerMode, kind, queryData],
  )
  const [selected, setSelected] = useState<Item | null>(null)
  const [editing, setEditing] = useState<Item | null | undefined>(undefined)
  const [deleting, setDeleting] = useState(false)
  const [search, setSearch] = useState('')
  const [builtinDirty, setBuiltinDirty] = useState(false)
  const [builtinDraftVersion, setBuiltinDraftVersion] = useState(0)
  const [formDirty, setFormDirty] = useState(false)
  const [formDraftVersion, setFormDraftVersion] = useState(0)
  const builtinDetailsRef = useRef<BuiltinAgentDetailsHandle>(null)
  const formId = `${kind}-form`
  const info = meta[kind]
  const selectedAgentPlacements =
    kind === 'agents' && !isBuiltins ? ((selected as Subagent | null)?.placement_count ?? 0) : 0
  const selectedIsManagedServer =
    kind === 'servers' && Boolean((selected as McpServer | null)?.managed)

  useEffect(() => {
    if ((location.state as { resetResourceList?: boolean } | null)?.resetResourceList) {
      setEditing(undefined)
      setSelected(null)
      setDeleting(false)
    }
  }, [location.key, location.state])

  useEffect(() => setSearch(''), [resourceMode, modelMode, kind])

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
    const found = data.find((item) => 'id' in item && item.id === id)
    if (found) setSelected(found)
  }, [params, data, isBuiltins])

  const refresh = async () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys[kind] }),
      client.invalidateQueries({ queryKey: keys.embeddingModels }),
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
      if (editing) {
        return api[kind].update((editing as ModelRecord | McpServer | Subagent).id, body)
      }
      return api[kind].create(body)
    },
    onSuccess: async (saved) => {
      await refresh()
      setFormDirty(false)
      const item = saved as Item
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
                  ...(isEmbeddingMode ? { view: 'embedding' } : {}),
                  open: String((item as ModelRecord | EmbeddingModelRecord).id),
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
      return api[kind].remove((selected as ModelRecord | McpServer | Subagent).id)
    },
    onSuccess: async () => {
      setParams(
        hasTypePicker ? { view: resourceMode } : isEmbeddingMode ? { view: 'embedding' } : {},
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
    },
  })
  const builtinConfig = useMutation<
    BuiltinAgent,
    Error,
    {
      model_id: number | null
      generation_model_id?: number | null
      mcp_server_id?: number | null
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
      setBuiltinDirty(false)
      setSelected(saved)
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
        const server = item as McpServer
        const agent = item as Subagent
        if (isEmbeddingMode)
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
    [data, isBuiltins, isEmbeddingMode, kind],
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
    setBuiltinDirty(false)
    setParams(hasTypePicker ? { view: resourceMode } : isEmbeddingMode ? { view: 'embedding' } : {})
  }
  const openCreate = () => {
    setSelected(null)
    setFormDirty(false)
    setEditing(null)
    setParams(hasTypePicker ? { view: 'dynamic' } : isEmbeddingMode ? { view: 'embedding' } : {})
  }
  const cancelForm = () => {
    setFormDirty(false)
    setEditing(undefined)
    if (!selected)
      setParams(
        hasTypePicker ? { view: resourceMode } : isEmbeddingMode ? { view: 'embedding' } : {},
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
    setBuiltinDirty(false)
    setDeleting(false)
    setSearch('')
    setParams({ view: mode })
  }

  const switchModelMode = (mode: ModelMode) => {
    if (mode === modelMode) return
    setEditing(undefined)
    setSelected(null)
    setDeleting(false)
    setSearch('')
    setParams(mode === 'embedding' ? { view: 'embedding' } : {})
  }

  const singular = isBuiltins
    ? 'built-in subagent'
    : isBuiltinServerMode
      ? 'built-in MCP server'
      : isEmbeddingMode
        ? 'embedding model'
        : info.singular
  const collectionTitle = isEmbeddingMode ? 'Embedding models' : info.title

  const inForm = editing !== undefined
  const headerTitle = inForm
    ? `${editing ? 'Edit' : 'Add'} ${singular}`
    : selected
      ? selected.name
      : info.title
  const headerDescription = inForm
    ? editing
      ? `Update the complete ${editing.name} configuration`
      : `Create a new ${singular}`
    : selected
      ? `Saved ${singular} configuration`
      : info.description

  const headerActions = inForm ? (
    <>
      {formDirty && (
        <Button
          className="resource-header-action"
          icon={<X size={15} />}
          onClick={() => {
            save.reset()
            setFormDirty(false)
            setFormDraftVersion((version) => version + 1)
          }}
          aria-label="Discard changes"
          title="Discard changes"
        />
      )}
      {(kind !== 'agents' || editing !== null) && (
        <Button
          variant="primary"
          icon={<Save size={14} />}
          type="submit"
          form={formId}
          busy={save.isPending}
        >
          {editing ? 'Save changes' : 'Create'}
        </Button>
      )}
    </>
  ) : selected ? (
    <div className="resource-header-actions">
      {isBuiltins && builtinDirty && (
        <>
          <Button
            className="resource-header-action"
            icon={<X size={15} />}
            disabled={builtinConfig.isPending}
            onClick={() => {
              builtinConfig.reset()
              setBuiltinDirty(false)
              setBuiltinDraftVersion((version) => version + 1)
            }}
            aria-label="Discard changes"
            title="Discard changes"
          />
          <Button
            className="resource-header-action"
            variant="primary"
            icon={<Save size={15} />}
            busy={builtinConfig.isPending}
            onClick={() => builtinDetailsRef.current?.save()}
            aria-label="Save changes"
            title="Save changes"
          />
        </>
      )}
      {!isBuiltins && !selectedIsManagedServer && (
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
      {!isBuiltins && !selectedIsManagedServer && (
        <Button
          className="resource-header-action"
          variant="primary"
          icon={<Edit3 size={15} />}
          onClick={() => {
            setFormDirty(false)
            setEditing(selected)
          }}
          aria-label={`Edit ${singular}`}
          title={`Edit ${singular}`}
        />
      )}
    </div>
  ) : !isBuiltins && !isBuiltinServerMode ? (
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
          selected && !inForm ? (
            <Button
              icon={<ChevronLeft size={15} />}
              onClick={openList}
              aria-label={`Back to ${collectionTitle.toLowerCase()}`}
              title={`Back to ${collectionTitle.toLowerCase()}`}
            />
          ) : inForm ? (
            <Button
              icon={<ChevronLeft size={15} />}
              onClick={cancelForm}
              aria-label={`Back to ${selected ? selected.name : collectionTitle.toLowerCase()}`}
              title={`Back to ${selected ? selected.name : collectionTitle.toLowerCase()}`}
            />
          ) : undefined
        }
        actions={headerActions}
      />
      <div className={`page-content ${hasTypePicker || kind === 'models' ? 'stack' : ''}`}>
        {kind === 'models' && !inForm && !selected && (
          <div className="model-location-picker" role="tablist" aria-label="Model type">
            <span
              className={`model-location-picker__indicator ${modelMode === 'embedding' ? 'is-local' : ''}`}
              aria-hidden="true"
            />
            <button
              type="button"
              role="tab"
              className={modelMode === 'language' ? 'is-active' : ''}
              aria-selected={modelMode === 'language'}
              onClick={() => switchModelMode('language')}
            >
              Language
            </button>
            <button
              type="button"
              role="tab"
              className={modelMode === 'embedding' ? 'is-active' : ''}
              aria-selected={modelMode === 'embedding'}
              onClick={() => switchModelMode('embedding')}
            >
              Embeddings
            </button>
          </div>
        )}
        {hasTypePicker && !inForm && !selected && (
          <div
            className="model-location-picker"
            role="tablist"
            aria-label={kind === 'agents' ? 'Subagent type' : 'MCP server type'}
          >
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
        {inForm ? (
          <section
            key={`resource-form:${formDraftVersion}`}
            className="card resource-workspace resource-workspace--form"
            onChangeCapture={() => setFormDirty(true)}
            onInputCapture={() => setFormDirty(true)}
            onClickCapture={(event) => {
              const button = (event.target as HTMLElement).closest('button[type="button"]')
              if (
                button &&
                !button.closest('.section-tabs, .subagent-wizard__steps, .subagent-form-footer')
              )
                setFormDirty(true)
            }}
          >
            <div className="card__body">
              {kind === 'models' && !isEmbeddingMode && (
                <ModelForm
                  item={editing as ModelRecord | undefined}
                  formId={formId}
                  onSubmit={submit}
                />
              )}
              {isEmbeddingMode && (
                <EmbeddingModelForm
                  item={editing as EmbeddingModelRecord | undefined}
                  formId={formId}
                  onSubmit={submit}
                />
              )}
              {kind === 'servers' && (
                <ServerForm
                  item={editing as McpServer | undefined}
                  formId={formId}
                  onSubmit={submit}
                />
              )}
              {kind === 'agents' && !isBuiltins && (
                <AgentForm
                  key={(editing as Subagent | undefined)?.id || 'new-agent'}
                  item={editing as Subagent | undefined}
                  models={models.data || []}
                  servers={servers.data || []}
                  formId={formId}
                  busy={save.isPending}
                  onSubmit={submit}
                />
              )}
            </div>
          </section>
        ) : selected ? (
          <section className="resource-detail-page">
            {isBuiltins ? (
              <BuiltinAgentDetails
                key={`${(selected as BuiltinAgent).key}:${builtinDraftVersion}`}
                ref={builtinDetailsRef}
                item={selected as BuiltinAgent}
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
                onDirtyChange={setBuiltinDirty}
                onSave={(body) => builtinConfig.mutateAsync(body)}
              />
            ) : isEmbeddingMode ? (
              <EmbeddingModelDetails
                item={selected as EmbeddingModelRecord}
                onTested={(saved) => setSelected(saved)}
              />
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
          <Feedback message={(query.error as Error).message} />
        ) : !data.length ? (
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
            {listed.length ? (
              <div className="resource-list">
                {listed.map(({ item, row }) => {
                  const isAgent = kind === 'agents'
                  return (
                    <button
                      className={`resource-row resource-row--${kind} ${row.status ? '' : 'resource-row--without-status'}`}
                      type="button"
                      key={'key' in item ? item.key : item.id}
                      onClick={() => {
                        setEditing(undefined)
                        setBuiltinDirty(false)
                        setSelected(item)
                        setParams(
                          isBuiltins
                            ? { view: 'built-in', builtin: (item as BuiltinAgent).key }
                            : {
                                ...(hasTypePicker ? { view: resourceMode } : {}),
                                ...(isEmbeddingMode ? { view: 'embedding' } : {}),
                                open: String(
                                  (
                                    item as
                                      ModelRecord | EmbeddingModelRecord | McpServer | Subagent
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
                        <span>
                          <strong>{row.title}</strong>
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
                No {collectionTitle.toLowerCase()} match “{search.trim()}”.
              </div>
            )}
          </div>
        )}
      </div>
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
