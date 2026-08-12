import { Bot } from 'lucide-react'
import type { Subagent } from '../../api/types'

export function AgentChoices({
  agents,
  selected,
  onChange,
}: {
  agents: Subagent[]
  selected: Set<number>
  onChange: (next: Set<number>) => void
}) {
  if (!agents.length)
    return <div className="empty-state agent-choice-empty">No subagents are available.</div>

  return (
    <div className="agent-choice-list">
      {agents.map((agent) => (
        <label className="agent-choice" key={agent.id}>
          <input
            type="checkbox"
            checked={selected.has(agent.id)}
            onChange={(event) => {
              const next = new Set(selected)
              event.target.checked ? next.add(agent.id) : next.delete(agent.id)
              onChange(next)
            }}
          />
          <span className="avatar">
            {agent.has_icon ? (
              <img src={`/api/subagents/${agent.id}/icon`} alt="" />
            ) : (
              <Bot size={16} />
            )}
          </span>
          <span className="agent-choice__copy">
            <strong>{agent.name}</strong>
            <small>{agent.description}</small>
            <small className="agent-choice__parent">
              Existing nodes: {(agent.placements || []).map((node) => node.path_label).join(', ')}
            </small>
          </span>
        </label>
      ))}
    </div>
  )
}
