import { useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { AgentPlacement, McpServer, ModelRecord, Subagent } from '../../api/types'
import { AutoTextarea } from '../../components/ui/AutoTextarea'
import { Field } from '../../components/ui/Field'
import { Feedback } from '../../components/ui/Feedback'
import { AgentChoices } from './AgentChoices'
import { stringList, toDataUrl } from './helpers'
import { ToolChoices } from './ToolChoices'

export function AgentForm({
  item,
  models,
  servers,
  agents,
  formId,
  onSubmit,
}: {
  item?: Subagent
  models: ModelRecord[]
  servers: McpServer[]
  agents: Subagent[]
  formId: string
  onSubmit: (body: object) => Promise<void>
}) {
  const existingConfirm = stringList(item?.confirm_tools, item?.confirm_tool_calls ? ['*'] : [])
  const [serverId, setServerId] = useState(item?.mcp_server_id || 0)
  const [mode, setMode] = useState(
    existingConfirm.includes('*') ? 'all' : existingConfirm.length ? 'selected' : 'none',
  )
  const [confirmed, setConfirmed] = useState(new Set(existingConfirm.filter((v) => v !== '*')))
  const [deduped, setDeduped] = useState(new Set(stringList(item?.dedupe_tools)))
  const [icon, setIcon] = useState<string | undefined>()
  const [error, setError] = useState('')
  const [parents, setParents] = useState<Set<number | null>>(() => {
    const initial = new Set<number | null>(
      (item?.placements || []).map((placement) => placement.parent_node_id),
    )
    if (!item || !initial.size) initial.add(null)
    return initial
  })
  const [placementChildren, setPlacementChildren] = useState(
    new Map<number, Set<number>>(
      (item?.placements || []).map((placement) => [
        placement.id,
        new Set(placement.child_agent_ids),
      ]),
    ),
  )
  const parentPlacements = agents.flatMap((agent) =>
    (agent.placements || []).map((placement) => ({ agent, placement })),
  )
  const availableParents = parentPlacements.filter(
    ({ agent, placement }) => agent.id !== item?.id && placement.depth < 4,
  )
  const availableChildren = agents.filter((agent) => agent.id !== item?.id)
  const tools = useQuery({
    queryKey: ['server-tools', serverId],
    queryFn: () => api.servers.tools(serverId),
    enabled: Boolean(serverId),
  })
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const body = Object.fromEntries(new FormData(event.currentTarget).entries()) as Record<
      string,
      unknown
    >
    try {
      body.model_id = Number(body.model_id)
      body.mcp_server_id = Number(body.mcp_server_id)
      if (!parents.size) throw new Error('Select at least one parent connection.')
      body.parent_node_ids = [...parents]
      delete body.confirmation_mode
      body.confirm_tools = mode === 'all' ? ['*'] : mode === 'selected' ? [...confirmed] : []
      if (mode === 'selected' && !confirmed.size)
        throw new Error('Select at least one action requiring confirmation.')
      body.confirm_tool_calls = mode !== 'none'
      body.dedupe_tools = [...deduped]
      if (item)
        body.placement_children = [...placementChildren]
          .filter(([nodeId]) => {
            const placement = item.placements?.find((candidate) => candidate.id === nodeId)
            return placement ? parents.has(placement.parent_node_id) : false
          })
          .map(([node_id, childIds]) => ({
            node_id,
            child_agent_ids: [...childIds],
          }))
      if (icon !== undefined) body.icon_data = icon
      await onSubmit(body)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the subagent.')
    }
  }
  return (
    <form id={formId} className="form-grid" onSubmit={submit}>
      {!models.length && <div className="guidance">Create a model before adding a subagent.</div>}
      {!servers.length && (
        <div className="guidance">Connect an MCP server before adding a subagent.</div>
      )}
      <Field full label="Name" hint="Choose a short name that describes this specialist.">
        <input name="name" defaultValue={item?.name} required />
      </Field>
      <Field full label="Icon" hint="Square PNG, JPEG, WebP, or GIF smaller than 512 KB.">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span className="avatar">
            {icon ? (
              <img src={icon} alt="Preview" />
            ) : item?.has_icon ? (
              <img src={`/api/subagents/${item.id}/icon`} alt="Current" />
            ) : (
              (item?.name || '?')[0]
            )}
          </span>
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            onChange={async (e) => {
              const file = e.target.files?.[0]
              if (!file) return
              if (file.size > 512 * 1024) {
                setError('The icon must be smaller than 512 KB.')
                return
              }
              setIcon(await toDataUrl(file))
            }}
          />
          <button className="button button--secondary" type="button" onClick={() => setIcon('')}>
            Remove
          </button>
        </div>
      </Field>
      <Field
        full
        label="Description"
        hint="Explain when the parent agent should use this specialist."
      >
        <AutoTextarea name="description" defaultValue={item?.description} required rows={2} />
      </Field>
      <Field
        full
        label="System prompt"
        hint="Optional instructions that apply only to this specialist."
      >
        <AutoTextarea name="system_prompt" defaultValue={item?.system_prompt} rows={6} />
      </Field>
      <Field label="Model">
        <select name="model_id" defaultValue={item?.model_id || ''} required>
          <option value="">Choose a model…</option>
          {models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.name} — {model.provider}
            </option>
          ))}
        </select>
      </Field>
      <Field label="MCP server">
        <select
          name="mcp_server_id"
          value={serverId || ''}
          required
          onChange={(e) => {
            setServerId(Number(e.target.value))
            setConfirmed(new Set())
            setDeduped(new Set())
          }}
        >
          <option value="">Choose a server…</option>
          {servers.map((server) => (
            <option key={server.id} value={server.id}>
              {server.name}
            </option>
          ))}
        </select>
      </Field>
      <Field full label="Parent connections" hint="Choose where this subagent appears.">
        <div className="agent-choice-list agent-parent-choice-list">
          <label className="agent-choice">
            <input
              type="checkbox"
              checked={parents.has(null)}
              onChange={(event) => {
                const next = new Set(parents)
                event.target.checked ? next.add(null) : next.delete(null)
                setParents(next)
              }}
            />
            <span className="avatar">M</span>
            <span className="agent-choice__copy">
              <strong>Mounir</strong>
              <small>Make this agent directly available to the orchestrator.</small>
            </span>
          </label>
          {availableParents.map(({ agent, placement }) => (
            <label className="agent-choice" key={placement.id}>
              <input
                type="checkbox"
                checked={parents.has(placement.id)}
                onChange={(event) => {
                  const next = new Set(parents)
                  event.target.checked ? next.add(placement.id) : next.delete(placement.id)
                  setParents(next)
                }}
              />
              <span className="avatar">
                {agent.has_icon ? (
                  <img src={`/api/subagents/${agent.id}/icon`} alt="" />
                ) : (
                  agent.name[0]
                )}
              </span>
              <span className="agent-choice__copy">
                <strong>{agent.name}</strong>
                <small>{placement.path_label}</small>
              </span>
            </label>
          ))}
        </div>
      </Field>
      {item &&
        (item.placements || []).map((placement: AgentPlacement) => (
          <section className="key-value-editor agent-children" key={placement.id}>
            <div className="key-value-editor__title">
              <span>
                <strong>Children of {placement.path_label}</strong>
                <small>Select the subagents connected here.</small>
              </span>
            </div>
            <AgentChoices
              agents={placement.depth >= 4 ? [] : availableChildren}
              selected={placementChildren.get(placement.id) || new Set()}
              onChange={(selected) => {
                setPlacementChildren((current) => {
                  const next = new Map(current)
                  next.set(placement.id, selected)
                  return next
                })
              }}
            />
          </section>
        ))}
      <Field full label="Action confirmation">
        <select name="confirmation_mode" value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="all">Ask before every action</option>
          <option value="selected">Ask for selected actions</option>
          <option value="none">Run without confirmation</option>
        </select>
      </Field>
      {mode === 'selected' && (
        <div className="key-value-editor">
          <strong>Actions requiring confirmation</strong>
          <ToolChoices
            tools={tools.data?.tools || []}
            selected={confirmed}
            onChange={setConfirmed}
          />
        </div>
      )}
      <div className="key-value-editor">
        <strong>Block identical repeated actions</strong>
        <ToolChoices tools={tools.data?.tools || []} selected={deduped} onChange={setDeduped} />
      </div>
      <div className="field--full">
        <Feedback message={error || (tools.error instanceof Error ? tools.error.message : '')} />
      </div>
    </form>
  )
}
