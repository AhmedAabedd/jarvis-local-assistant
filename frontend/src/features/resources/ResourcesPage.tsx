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
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import type { BuiltinAgent, McpServer, ModelRecord, Subagent } from '../../api/types'
import { McpIcon } from '../../components/icons/McpIcon'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Loading } from '../../components/ui/Loading'
import { Status } from '../../components/ui/Status'
import { useAgents, useBuiltins, useModels, useServers, keys } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'
import { AgentForm } from './AgentForm'
import { BuiltinAgentDetails } from './BuiltinAgentDetails'
import { ModelForm } from './ModelForm'
import { ResourceDetails } from './ResourceDetails'
import { ServerForm } from './ServerForm'

type Kind = 'models' | 'servers' | 'agents'
type Item = ModelRecord | McpServer | Subagent | BuiltinAgent
type AgentMode = 'built-in' | 'dynamic'

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

export function ResourcesPage({ kind }: { kind: Kind }) {
  const client = useQueryClient()
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const models = useModels()
  const servers = useServers()
  const agents = useAgents()
  const builtins = useBuiltins()
  const [agentMode, setAgentMode] = useState<AgentMode>(
    params.get('view') === 'built-in' ? 'built-in' : 'dynamic',
  )
  const isBuiltins = kind === 'agents' && agentMode === 'built-in'
  const query = isBuiltins
    ? builtins
    : kind === 'models'
      ? models
      : kind === 'servers'
        ? servers
        : agents
  const data = (query.data || []) as Item[]
  const [selected, setSelected] = useState<Item | null>(null)
  const [editing, setEditing] = useState<Item | null | undefined>(undefined)
  const [deleting, setDeleting] = useState(false)
  const [search, setSearch] = useState('')
  const formId = `${kind}-form`
  const info = meta[kind]
  const selectedAgentPlacements =
    kind === 'agents' && !isBuiltins
      ? ((selected as Subagent | null)?.placement_count ?? 0)
      : 0

  useEffect(() => {
    if ((location.state as { resetResourceList?: boolean } | null)?.resetResourceList) {
      setEditing(undefined)
      setSelected(null)
      setDeleting(false)
    }
  }, [location.key, location.state])

  useEffect(() => {
    if (kind !== 'agents') return
    const requested = params.get('view')
    if ((requested === 'built-in' || requested === 'dynamic') && requested !== agentMode) {
      setAgentMode(requested)
      setEditing(undefined)
      setSelected(null)
    }
  }, [agentMode, kind, params])

  useEffect(() => setSearch(''), [agentMode, kind])

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
      client.invalidateQueries({ queryKey: keys.builtins }),
      client.invalidateQueries({ queryKey: keys.overview }),
      client.invalidateQueries({ queryKey: keys.agentNodes }),
    ])

  const save = useMutation({
    mutationFn: async (body: object) => {
      if (isBuiltins) {
        if (!editing) throw new Error('No built-in subagent is selected.')
        return api.builtins.update((editing as BuiltinAgent).key, body)
      }
      if (editing) {
        return api[kind].update((editing as ModelRecord | McpServer | Subagent).id, body)
      }
      return api[kind].create(body)
    },
    onSuccess: async (saved) => {
      await refresh()
      const item = saved as Item
      setEditing(undefined)
      setSelected(item)
      setParams(
        isBuiltins
          ? { view: 'built-in', builtin: (item as BuiltinAgent).key }
          : kind === 'agents'
            ? { view: 'dynamic', open: String((item as Subagent).id) }
            : { open: String((item as ModelRecord | McpServer).id) },
      )
    },
  })
  const remove = useMutation({
    mutationFn: () => {
      if (!selected || isBuiltins) return Promise.resolve()
      return api[kind].remove((selected as ModelRecord | McpServer | Subagent).id)
    },
    onSuccess: async () => {
      setParams({})
      await refresh()
      setSelected(null)
      setDeleting(false)
    },
  })
  const toggle = useMutation<Item, Error, boolean>({
    mutationFn: (enabled: boolean) => {
      if (!selected || kind !== 'agents') throw new Error('No subagent is selected.')
      return isBuiltins
        ? api.builtins.update((selected as BuiltinAgent).key, { enabled })
        : api.agents.update((selected as Subagent).id, { enabled })
    },
    onSuccess: async (saved) => {
      await refresh()
      setSelected(saved)
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
  const builtinModels = useMutation<
    BuiltinAgent,
    Error,
    { model_id: number | null; generation_model_id?: number | null }
  >({
    mutationFn: (body) => {
      if (!selected || !isBuiltins) throw new Error('No built-in subagent is selected.')
      return api.builtins.update((selected as BuiltinAgent).key, body)
    },
    onSuccess: async (saved) => {
      await refresh()
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
        const server = item as McpServer
        const agent = item as Subagent
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
    setParams(kind === 'agents' ? { view: agentMode } : {})
  }
  const openCreate = () => {
    setSelected(null)
    setEditing(null)
    setParams(kind === 'agents' ? { view: 'dynamic' } : {})
  }
  const cancelForm = () => {
    setEditing(undefined)
    if (!selected) setParams(kind === 'agents' ? { view: agentMode } : {})
  }
  const submit = async (body: object) => {
    await save.mutateAsync(
      kind === 'agents' && !isBuiltins && !editing
        ? { ...body, connect_to_workflow: false }
        : body,
    )
  }

  const switchAgentMode = (mode: AgentMode) => {
    setAgentMode(mode)
    setEditing(undefined)
    setSelected(null)
    setDeleting(false)
    setSearch('')
    setParams({ view: mode })
  }

  const singular = isBuiltins ? 'built-in subagent' : info.singular

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
      <Button icon={<ChevronLeft size={14} />} onClick={cancelForm}>
        Cancel
      </Button>
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
      <Button
        className="resource-header-action"
        icon={<ChevronLeft size={15} />}
        onClick={openList}
        aria-label={`Back to ${info.title.toLowerCase()}`}
        title={`Back to ${info.title.toLowerCase()}`}
      />
      {!isBuiltins && (
        <Button
          className="resource-header-action"
          icon={<Trash2 size={15} />}
          onClick={() => {
            remove.reset()
            setDeleting(true)
          }}
          aria-label={`Delete ${info.singular}`}
          title={`Delete ${info.singular}`}
        />
      )}
      {!isBuiltins && (
        <Button
          className="resource-header-action"
          variant="primary"
          icon={<Edit3 size={15} />}
          onClick={() => setEditing(selected)}
          aria-label={`Edit ${info.singular}`}
          title={`Edit ${info.singular}`}
        />
      )}
    </div>
  ) : (
    !isBuiltins ? (
      <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
        Add {info.singular}
      </Button>
    ) : null
  )

  return (
    <>
      <PageHeader title={headerTitle} description={headerDescription} actions={headerActions} />
      <div className={`page-content ${kind === 'agents' ? 'stack' : ''}`}>
        {kind === 'agents' && (
          <div className="model-location-picker" role="tablist" aria-label="Subagent type">
            <span
              className={`model-location-picker__indicator ${agentMode === 'built-in' ? 'is-local' : ''}`}
              aria-hidden="true"
            />
            <button
              type="button"
              role="tab"
              className={agentMode === 'dynamic' ? 'is-active' : ''}
              aria-selected={agentMode === 'dynamic'}
              onClick={() => switchAgentMode('dynamic')}
            >
              Dynamic
            </button>
            <button
              type="button"
              role="tab"
              className={agentMode === 'built-in' ? 'is-active' : ''}
              aria-selected={agentMode === 'built-in'}
              onClick={() => switchAgentMode('built-in')}
            >
              Built-in
            </button>
          </div>
        )}
        {inForm ? (
          <section className="card resource-workspace resource-workspace--form">
            <div className="card__body">
              {kind === 'models' && (
                <ModelForm
                  item={editing as ModelRecord | undefined}
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
              {kind === 'agents' && (
                !isBuiltins && (
                  <AgentForm
                    key={(editing as Subagent | undefined)?.id || 'new-agent'}
                    item={editing as Subagent | undefined}
                    models={models.data || []}
                    servers={servers.data || []}
                    formId={formId}
                    busy={save.isPending}
                    onSubmit={submit}
                  />
                )
              )}
            </div>
          </section>
        ) : selected ? (
          <section className="resource-detail-page">
            {isBuiltins ? (
              <BuiltinAgentDetails
                key={(selected as BuiltinAgent).key}
                item={selected as BuiltinAgent}
                models={models.data || []}
                saving={builtinModels.isPending}
                connecting={builtinConnect.isPending}
                error={
                  builtinModels.error instanceof Error
                    ? builtinModels.error.message
                    : builtinConnect.error instanceof Error
                      ? builtinConnect.error.message
                      : ''
                }
                onConnect={(connected) => builtinConnect.mutate(connected)}
                onSave={(body) => builtinModels.mutateAsync(body)}
              />
            ) : (
              <ResourceDetails
                kind={kind}
                item={selected as ModelRecord | McpServer | Subagent}
                models={models.data || []}
                onToggle={(enabled) => toggle.mutate(enabled)}
              />
            )}
            <Feedback message={toggle.error instanceof Error ? toggle.error.message : ''} />
          </section>
        ) : query.isLoading ? (
          <Loading />
        ) : query.error ? (
          <Feedback message={(query.error as Error).message} />
        ) : !data.length ? (
          <div className="card empty-state">No {info.title.toLowerCase()} saved yet.</div>
        ) : (
          <div className="resource-browser">
            <label className="resource-search">
              <Search size={13} />
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder={`Search ${info.title.toLowerCase()}…`}
                aria-label={`Search ${info.title.toLowerCase()}`}
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
                        setSelected(item)
                        setParams(
                          isBuiltins
                            ? { view: 'built-in', builtin: (item as BuiltinAgent).key }
                            : {
                                ...(kind === 'agents' ? { view: 'dynamic' } : {}),
                                open: String((item as ModelRecord | McpServer | Subagent).id),
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
                No {info.title.toLowerCase()} match “{search.trim()}”.
              </div>
            )}
          </div>
        )}
      </div>
      <ConfirmDialog
        open={deleting}
        title={`Delete ${info.singular}?`}
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
