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
import type { McpServer, ModelRecord, Subagent } from '../../api/types'
import { McpIcon } from '../../components/icons/McpIcon'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Loading } from '../../components/ui/Loading'
import { Status } from '../../components/ui/Status'
import { useAgents, useModels, useServers, keys } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'
import { AgentForm } from './AgentForm'
import { ModelForm } from './ModelForm'
import { ResourceDetails } from './ResourceDetails'
import { ServerForm } from './ServerForm'

type Kind = 'models' | 'servers' | 'agents'
type Item = ModelRecord | McpServer | Subagent

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
  const models = useModels()
  const servers = useServers()
  const agents = useAgents()
  const query = kind === 'models' ? models : kind === 'servers' ? servers : agents
  const data = (query.data || []) as Item[]
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const [selected, setSelected] = useState<Item | null>(null)
  const [editing, setEditing] = useState<Item | null | undefined>(undefined)
  const [deleting, setDeleting] = useState(false)
  const [search, setSearch] = useState('')
  const formId = `${kind}-form`
  const info = meta[kind]
  const selectedAgentPlacements =
    kind === 'agents' ? ((selected as Subagent | null)?.placement_count ?? 0) : 0

  useEffect(() => {
    if ((location.state as { resetResourceList?: boolean } | null)?.resetResourceList) {
      setEditing(undefined)
      setSelected(null)
      setDeleting(false)
    }
  }, [location.key, location.state])

  useEffect(() => setSearch(''), [kind])

  useEffect(() => {
    const id = Number(params.get('open'))
    if (!id || !data.length) return
    const found = data.find((item) => item.id === id)
    if (found) setSelected(found)
  }, [params, data])

  const refresh = async () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys[kind] }),
      client.invalidateQueries({ queryKey: keys.overview }),
      client.invalidateQueries({ queryKey: keys.agentNodes }),
    ])

  const save = useMutation({
    mutationFn: async (body: object) =>
      editing ? api[kind].update(editing.id, body) : api[kind].create(body),
    onSuccess: async (saved) => {
      await refresh()
      const item = saved as Item
      setEditing(undefined)
      setSelected(item)
      setParams({ open: String(item.id) })
    },
  })
  const remove = useMutation({
    mutationFn: () => (selected ? api[kind].remove(selected.id) : Promise.resolve()),
    onSuccess: async () => {
      setParams({})
      await refresh()
      setSelected(null)
      setDeleting(false)
    },
  })
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => {
      if (!selected || kind !== 'agents') throw new Error('No subagent is selected.')
      return api.agents.update(selected.id, { enabled })
    },
    onSuccess: async (saved) => {
      await refresh()
      setSelected(saved)
    },
  })

  const rows = useMemo(
    () =>
      data.map((item) => {
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
    [data, kind],
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
    setParams({})
  }
  const openCreate = () => {
    setSelected(null)
    setEditing(null)
    setParams({})
  }
  const cancelForm = () => {
    setEditing(undefined)
    if (!selected) setParams({})
  }
  const submit = async (body: object) => {
    await save.mutateAsync(
      kind === 'agents' && !editing ? { ...body, connect_to_workflow: false } : body,
    )
  }

  const inForm = editing !== undefined
  const headerTitle = inForm
    ? `${editing ? 'Edit' : 'Add'} ${info.singular}`
    : selected
      ? selected.name
      : info.title
  const headerDescription = inForm
    ? editing
      ? `Update the complete ${editing.name} configuration`
      : `Create a new ${info.singular}`
    : selected
      ? `Saved ${info.singular} configuration`
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
      <Button
        className="resource-header-action"
        variant="primary"
        icon={<Edit3 size={15} />}
        onClick={() => setEditing(selected)}
        aria-label={`Edit ${info.singular}`}
        title={`Edit ${info.singular}`}
      />
    </div>
  ) : (
    <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
      Add {info.singular}
    </Button>
  )

  return (
    <>
      <PageHeader title={headerTitle} description={headerDescription} actions={headerActions} />
      <div className="page-content">
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
            <ResourceDetails
              kind={kind}
              item={selected}
              models={models.data || []}
              onToggle={(enabled) => toggle.mutate(enabled)}
            />
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
                      key={item.id}
                      onClick={() => {
                        setEditing(undefined)
                        setSelected(item)
                        setParams({ open: String(item.id) })
                      }}
                    >
                      <div className="resource-row__identity">
                        {isAgent && (
                          <span className="avatar">
                            {(item as Subagent).has_icon ? (
                              <img src={`/api/subagents/${item.id}/icon`} alt="" />
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
