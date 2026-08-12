import { Bot, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { Subagent } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Modal } from '../../components/ui/Modal'

const MAX_AGENT_DEPTH = 4

function availableConnections(
  agents: Subagent[],
  parentNodeId: number | null,
  parentAgentId: number | null,
) {
  const parentDepth = parentNodeId
    ? agents
        .flatMap((agent) => agent.placements || [])
        .find((placement) => placement.id === parentNodeId)?.depth || MAX_AGENT_DEPTH
    : 0
  if (parentDepth >= MAX_AGENT_DEPTH) return []
  return agents.filter((agent) => {
    if (agent.id === parentAgentId) return false
    return !(agent.placements || []).some((placement) => placement.parent_node_id === parentNodeId)
  })
}

export function ConnectAgentModal({
  open,
  parentNodeId,
  parentAgentId,
  parentName,
  agents,
  busy,
  error,
  onSelect,
  onClose,
}: {
  open: boolean
  parentNodeId: number | null
  parentAgentId: number | null
  parentName: string
  agents: Subagent[]
  busy?: boolean
  error?: string
  onSelect: (agent: Subagent) => void
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  useEffect(() => {
    if (open) setQuery('')
  }, [open, parentNodeId, parentAgentId])

  const candidates = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    return availableConnections(agents, parentNodeId, parentAgentId).filter(
      (agent) =>
        !normalized ||
        agent.name.toLowerCase().includes(normalized) ||
        agent.description.toLowerCase().includes(normalized),
    )
  }, [agents, parentNodeId, parentAgentId, query])

  return (
    <Modal
      open={open}
      title="Connect subagent"
      description={`Select a subagent for ${parentName}.`}
      onClose={onClose}
      footer={<Button onClick={onClose}>Cancel</Button>}
    >
      <div className="connect-agent-picker">
        <label className="connect-agent-search">
          <Search size={14} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search subagents…"
            autoFocus
          />
        </label>
        <div className="connect-agent-list">
          {candidates.length ? (
            candidates.map((agent) => (
              <button
                className="connect-agent-option"
                key={agent.id}
                onClick={() => onSelect(agent)}
                disabled={busy}
              >
                <span className="avatar">
                  {agent.has_icon ? (
                    <img src={`/api/subagents/${agent.id}/icon`} alt="" />
                  ) : (
                    <Bot size={16} />
                  )}
                </span>
                <span>
                  <strong>{agent.name}</strong>
                  <small>{agent.description}</small>
                  <small className="connect-agent-option__parent">
                    Existing nodes:{' '}
                    {(agent.placements || []).map((node) => node.path_label).join(', ')}
                  </small>
                </span>
              </button>
            ))
          ) : (
            <div className="empty-state connect-agent-empty">
              {query ? 'No matching subagents.' : 'No safe subagents are available to connect.'}
            </div>
          )}
        </div>
        <Feedback message={error} />
      </div>
    </Modal>
  )
}
