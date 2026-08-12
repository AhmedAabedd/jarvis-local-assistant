import { X } from 'lucide-react'
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

  return createPortal(
    <aside
      className="node-drawer agent-config-drawer"
      role="dialog"
      aria-modal="false"
      aria-labelledby="agent-config-title"
    >
      <header className="node-drawer__header agent-config-drawer__header">
        <div>
          <h2 id="agent-config-title">{name}</h2>
          {description && <p>{description}</p>}
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close">
          <X size={18} />
        </button>
      </header>

      <div className="node-drawer__body">
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
