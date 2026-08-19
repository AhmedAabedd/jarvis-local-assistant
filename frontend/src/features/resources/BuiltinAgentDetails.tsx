import { Save } from 'lucide-react'
import { useState } from 'react'
import type { BuiltinAgent, ModelRecord } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { readable } from './helpers'

function Detail({ label, value, full = false }: { label: string; value: string; full?: boolean }) {
  return (
    <div className={`detail ${full ? 'detail--full' : ''}`}>
      <dt>{label}</dt>
      <dd>{value || 'Not configured'}</dd>
    </div>
  )
}

export function BuiltinAgentDetails({
  item,
  models,
  saving,
  connecting,
  error,
  onConnect,
  onSave,
}: {
  item: BuiltinAgent
  models: ModelRecord[]
  saving?: boolean
  connecting?: boolean
  error?: string
  onConnect: (connected: boolean) => void
  onSave: (body: { model_id: number | null; generation_model_id?: number | null }) => Promise<unknown>
}) {
  const [modelId, setModelId] = useState(Number(item.model_id || 0))
  const [generationModelId, setGenerationModelId] = useState(
    Number(item.generation_model_id || 0),
  )
  const dirty =
    modelId !== Number(item.model_id || 0) ||
    (item.key === 'media' && generationModelId !== Number(item.generation_model_id || 0))

  return (
    <div className="stack">
      <section className="card resource-workspace">
        <div className="setting-row">
          <span>
            <strong>{item.connected ? 'Connected' : 'Disconnected'}</strong>
            <small>Disconnected built-ins disappear from Overview and cannot receive tasks.</small>
          </span>
          <label className="switch">
            <input
              type="checkbox"
              checked={item.connected}
              disabled={connecting}
              onChange={(event) => onConnect(event.target.checked)}
            />
            <span className="switch__track"><span /></span>
          </label>
        </div>
        <div className="card__body detail-grid resource-detail-body">
          <Detail label="Overview state" value={item.enabled ? 'Active' : 'Inactive'} />
          <div className="detail">
            <dt>Analysis model</dt>
            <dd>
              <select value={modelId || ''} onChange={(event) => setModelId(Number(event.target.value))}>
                <option value="">Installation fallback</option>
                {models.map((model) => (
                  <option key={model.id} value={model.id}>{model.name} — {model.provider}</option>
                ))}
              </select>
            </dd>
          </div>
          {item.key === 'media' && (
            <div className="detail">
              <dt>Image generation model</dt>
              <dd>
                <select
                  value={generationModelId || ''}
                  onChange={(event) => setGenerationModelId(Number(event.target.value))}
                >
                  <option value="">Not configured</option>
                  {models.map((model) => (
                    <option key={model.id} value={model.id}>{model.name} — {model.provider}</option>
                  ))}
                </select>
              </dd>
            </div>
          )}
          <Detail label="Description" value={item.description} full />
          <Detail label="System prompt" value={item.system_prompt} full />
          <div className="detail detail--full">
            <dt>Built-in tools</dt>
            <dd>
              <div className="chips">
                {item.tools?.length ? item.tools.map((tool) => (
                  <span className="chip" key={tool.name}>{tool.label || readable(tool.name)}</span>
                )) : <span className="chip">No registered tools</span>}
              </div>
            </dd>
          </div>
          {dirty && (
            <div className="detail detail--full">
              <dt>Unsaved model changes</dt>
              <dd>
                <Button
                  variant="primary"
                  icon={<Save size={14} />}
                  busy={saving}
                  onClick={() => onSave({
                    model_id: modelId || null,
                    ...(item.key === 'media'
                      ? { generation_model_id: generationModelId || null }
                      : {}),
                  })}
                >
                  Save model changes
                </Button>
              </dd>
            </div>
          )}
        </div>
      </section>
      <Feedback message={error || ''} />
    </div>
  )
}
