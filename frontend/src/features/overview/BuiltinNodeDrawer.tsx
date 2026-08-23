import { BookOpen, Bot, ChevronRight, Cpu, Image as ImageIcon, Settings, X } from 'lucide-react'
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { BuiltinAgent } from '../../api/types'
import { ToolChoices } from '../resources/ToolChoices'

export function BuiltinNodeDrawer({
  agent,
  onOpenSubagent,
  onClose,
}: {
  agent: BuiltinAgent | null
  onOpenSubagent: (agentKey: string) => void
  onClose: () => void
}) {
  useEffect(() => {
    if (!agent) return
    const closeOnEscape = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [agent, onClose])

  if (!agent) return null

  const IdentityIcon =
    agent.key === 'media'
      ? ImageIcon
      : agent.key === 'knowledge'
        ? BookOpen
        : agent.key === 'system'
          ? Settings
          : Bot

  return createPortal(
    <aside className="node-drawer" role="dialog" aria-modal="false" aria-labelledby="builtin-title">
      <header className="node-drawer__compact-header">
        <a
          className="node-drawer__open-link"
          href={`/admin/agents?view=built-in&builtin=${agent.key}`}
          onClick={(event) => {
            event.preventDefault()
            onOpenSubagent(agent.key)
          }}
        >
          Open subagent <ChevronRight size={13} aria-hidden="true" />
        </a>
        <button
          className="icon-button panel-close-button"
          type="button"
          onClick={onClose}
          aria-label="Close"
        >
          <X size={14} />
        </button>
      </header>

      <div className="node-drawer__body">
        <div className="node-detail-identity">
          <span className="avatar">
            <IdentityIcon size={18} />
          </span>
          <span>
            <strong id="builtin-title">{agent.name}</strong>
            <small>{agent.description}</small>
          </span>
        </div>

        <div className="node-detail-model">
          <Cpu size={12} />
          <small>{agent.key === 'media' ? 'Analysis model' : 'Model'}</small>
          <strong title={agent.model || ''}>{agent.model || 'Installation fallback'}</strong>
        </div>

        <div className="node-tools">
          <div className="node-tools__title">
            <span className="field__label">Tools</span>
          </div>

          <div className="node-tool-list agent-config-drawer__capabilities">
            <ToolChoices
              tools={agent.tools || []}
              readOnly
              empty="No built-in tools are currently available."
            />
          </div>
        </div>
      </div>
    </aside>,
    document.body,
  )
}
