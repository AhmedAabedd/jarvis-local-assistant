import { Wrench } from 'lucide-react'
import type { ToolInfo } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { Switch } from '../../components/ui/Switch'

type ModelOption = { id: number; label: string }
type Availability = { enabled: boolean; onChange: (enabled: boolean) => void }

export function AgentConfigModal({
  open,
  name,
  description,
  modelId,
  modelOptions,
  tools,
  availability,
  capabilitiesLabel = 'Capabilities',
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
  return (
    <Modal
      open={open}
      title={name}
      description={description}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          <Button variant="primary" onClick={onSave} busy={busy} disabled={!modelId}>
            Save model
          </Button>
        </>
      }
    >
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
          <span className="field__label">
            <Wrench size={13} /> {capabilitiesLabel}
          </span>
          <div className="chips" style={{ marginTop: 10 }}>
            {(tools || []).map((tool) => (
              <span className="chip" key={tool.name} title={tool.description}>
                {tool.name.replaceAll('_', ' ')}
              </span>
            ))}
          </div>
        </div>
        <Feedback message={error} />
      </div>
    </Modal>
  )
}
