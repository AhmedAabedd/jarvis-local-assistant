import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronLeft, Database, Edit3, Plus, Save, Server, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import type { McpServer, ModelRecord, Subagent } from '../../api/types'
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
    icon: Database,
  },
  servers: {
    title: 'MCP Servers',
    singular: 'MCP server',
    description: 'Manage services and tools connected through MCP',
    icon: Server,
  },
  agents: {
    title: 'Subagents',
    singular: 'subagent',
    description: 'Manage specialists the supervisor can delegate work to',
    icon: Bot,
  },
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
  const formId = `${kind}-form`
  const info = meta[kind]

  useEffect(() => {
    if ((location.state as { resetResourceList?: boolean } | null)?.resetResourceList) {
      setEditing(undefined)
      setSelected(null)
      setDeleting(false)
    }
  }, [location.key, location.state])

  useEffect(() => {
    const id = Number(params.get('open'))
    if (!id || !data.length) return
    const found = data.find((item) => item.id === id)
    if (found) setSelected(found)
  }, [params, data])

  const refresh = async () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys[kind] }),
      kind !== 'models' && client.invalidateQueries({ queryKey: keys.overview }),
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
            subtitle: model.model,
            meta: model.provider,
            status:
              model.api_key_configured || model.api_key ? 'Credential saved' : 'No credential',
          }
        if (kind === 'servers')
          return {
            title: server.name,
            subtitle: server.description || server.connection,
            meta: server.transport?.replaceAll('_', ' '),
            status: server.connection_status || 'untested',
          }
        return {
          title: agent.name,
          subtitle: agent.description,
          meta: agent.mcp_server_name || 'MCP specialist',
          status: agent.enabled ? 'Active' : 'Inactive',
        }
      }),
    [data, kind],
  )
  const listed = useMemo(() => {
    const entries = data.map((item, index) => ({ item, index, depth: 0 }))
    if (kind !== 'agents') return entries
    const ids = new Set(data.map((item) => item.id))
    const children = new Map<number | null, typeof entries>()
    entries.forEach((entry) => {
      const parent = Number((entry.item as Subagent).parent_agent_id) || null
      const key = parent && ids.has(parent) ? parent : null
      children.set(key, [...(children.get(key) || []), entry])
    })
    const result: typeof entries = []
    const visited = new Set<number>()
    const append = (parent: number | null, depth: number) => {
      ;(children.get(parent) || []).forEach((entry) => {
        if (visited.has(entry.item.id)) return
        visited.add(entry.item.id)
        result.push({ ...entry, depth })
        append(entry.item.id, depth + 1)
      })
    }
    append(null, 0)
    entries.forEach((entry) => {
      if (!visited.has(entry.item.id)) result.push(entry)
    })
    return result
  }, [data, kind])

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
    await save.mutateAsync(body)
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
      <Button
        variant="primary"
        icon={<Save size={14} />}
        type="submit"
        form={formId}
        busy={save.isPending}
      >
        {editing ? 'Save changes' : 'Create'}
      </Button>
    </>
  ) : selected ? (
    <>
      <Button icon={<ChevronLeft size={14} />} onClick={openList}>
        Back to {info.title.toLowerCase()}
      </Button>
      <Button
        variant="danger"
        icon={<Trash2 size={14} />}
        onClick={() => {
          remove.reset()
          setDeleting(true)
        }}
      >
        Delete
      </Button>
      <Button variant="primary" icon={<Edit3 size={14} />} onClick={() => setEditing(selected)}>
        Edit
      </Button>
    </>
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
                  item={editing as Subagent | undefined}
                  models={models.data || []}
                  servers={servers.data || []}
                  agents={agents.data || []}
                  formId={formId}
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
              servers={servers.data || []}
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
          <div className="resource-list">
            {listed.map(({ item, index, depth }) => {
              const row = rows[index]
              const Icon = info.icon
              const isAgent = kind === 'agents'
              return (
                <button
                  className={`resource-row ${isAgent ? 'resource-row--agent' : ''}`}
                  key={item.id}
                  style={{ '--agent-depth': depth } as CSSProperties}
                  data-depth={depth || undefined}
                  onClick={() => {
                    setEditing(undefined)
                    setSelected(item)
                    setParams({ open: String(item.id) })
                  }}
                >
                  <div className="resource-row__identity">
                    <span className="avatar">
                      {isAgent && (item as Subagent).has_icon ? (
                        <img src={`/api/subagents/${item.id}/icon`} alt="" />
                      ) : (
                        <Icon size={17} />
                      )}
                    </span>
                    <span>
                      <strong>{row.title}</strong>
                      <small>{row.subtitle}</small>
                    </span>
                  </div>
                  <span className="resource-row__meta">{row.meta}</span>
                  <Status value={row.status} />
                </button>
              )
            })}
          </div>
        )}
      </div>
      <ConfirmDialog
        open={deleting}
        title={`Delete ${info.singular}?`}
        message={`This permanently removes “${selected?.name || ''}”. Records in use cannot be deleted.`}
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
