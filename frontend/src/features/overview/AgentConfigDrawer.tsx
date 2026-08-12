import { BookOpen, Bot, Image as ImageIcon, Settings, X } from 'lucide-react'
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { ToolInfo } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Switch } from '../../components/ui/Switch'
import { ToolChoices } from '../resources/ToolChoices'

type ModelOption = { id: number; label: string }
type Availability = { enabled: boolean; onChange: (enabled: boolean) => void }

export function AgentConfigDrawer({
  open,
  name,
  description,
  agentKey,
  modelId,
  modelOptions,
  tools,
  availability,
  capabilitiesLabel = 'Tools',
  modelHint,
  busy,
  error,
  onModelChange,
  onSave,
  onClose,
}: {
  open: boolean
  name: string
  description?: string
  agentKey?: string
  modelId: number
  modelOptions?: ModelOption[]
  tools?: ToolInfo[]
  availability?: Availability
  capabilitiesLabel?: string
  modelHint: string
  busy?: boolean
  error?: string
  onModelChange: (modelId: number) => void
  onSave: () => void
  onClose: () => void
}) {
  useEffect(() => {
    if (!open) return
    const closeOnEscape = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [open, onClose])

  if (!open) return null

  const IdentityIcon =
    agentKey === 'media'
      ? ImageIcon
      : agentKey === 'knowledge'
        ? BookOpen
        : agentKey === 'system'
          ? Settings
          : Bot

  return createPortal(
    <aside
      className="node-drawer agent-config-drawer"
      role="dialog"
      aria-modal="false"
      aria-labelledby="agent-config-title"
    >
      <header className="agent-config-drawer__topbar">
        <div className="agent-config-drawer__title">
          <span className="avatar">
            <IdentityIcon size={18} />
          </span>
          <h2 id="agent-config-title">{name}</h2>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </header>

      <div className="node-drawer__body">
        {description && (
          <div className="agent-config-drawer__description">
            <span className="field__label">Description</span>
            <p>{description}</p>
          </div>
        )}
        <div className="stack">
          {availability && (
            <div className="availability">
              <span>
                <strong>{availability.enabled ? 'Active' : 'Inactive'}</strong>
                <small>Control whether the supervisor can delegate to this specialist.</small>
              </span>
              <Switch
                checked={availability.enabled}
                label="Agent availability"
                onChange={availability.onChange}
              />
            </div>
          )}
          <Field label="Model" hint={modelHint}>
            <select value={modelId} onChange={(event) => onModelChange(Number(event.target.value))}>
              <option value={0}>Choose a model…</option>
              {(modelOptions || []).map((model) => (
                <option value={model.id} key={model.id}>
                  {model.label}
                </option>
              ))}
            </select>
          </Field>
          <div>
            <span className="field__label">{capabilitiesLabel}</span>
            <div className="node-tool-list agent-config-drawer__capabilities">
              <ToolChoices tools={tools || []} readOnly empty="No capabilities available." />
            </div>
          </div>
          <Feedback message={error} />
        </div>
      </div>

      <footer className="node-drawer__footer">
        <Button onClick={onClose}>Close</Button>
        <Button variant="primary" onClick={onSave} busy={busy} disabled={!modelId}>
          Save model
        </Button>
      </footer>
    </aside>,
    document.body,
  )
}
