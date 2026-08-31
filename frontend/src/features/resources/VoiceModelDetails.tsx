import { useMutation, useQueryClient } from '@tanstack/react-query'
import { PlugZap } from 'lucide-react'
import { useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { VoiceModelRecord } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Status } from '../../components/ui/Status'
import { keys } from '../../hooks/useStudioData'
import { readable } from './helpers'

function Detail({ label, value, full = false }: { label: string; value?: string; full?: boolean }) {
  return (
    <div className={`detail ${full ? 'detail--full' : ''}`}>
      <dt>{label}</dt>
      <dd>{value || 'Not configured'}</dd>
    </div>
  )
}

function testedModel(error: Error): VoiceModelRecord | undefined {
  if (!(error instanceof ApiError)) return undefined
  const model = error.data.model
  if (!model || typeof model !== 'object' || !('id' in model)) return undefined
  return model as VoiceModelRecord
}

export function VoiceModelDetails({
  item,
  onTested,
}: {
  item: VoiceModelRecord
  onTested: (saved: VoiceModelRecord) => void
}) {
  const client = useQueryClient()
  const [message, setMessage] = useState('')
  const test = useMutation({
    mutationFn: () => api.voiceModels.test(item.id),
    onSuccess: async (result) => {
      setMessage(result.message)
      onTested(result.model)
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.voiceModels }),
        client.invalidateQueries({ queryKey: keys.modelCatalog }),
      ])
    },
    onError: async (error) => {
      setMessage('')
      const saved = testedModel(error)
      if (saved) onTested(saved)
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.voiceModels }),
        client.invalidateQueries({ queryKey: keys.modelCatalog }),
      ])
    },
  })
  const options = Object.entries(item.provider_options || {})

  return (
    <div className="stack">
      <section className="card resource-workspace">
        <div className="setting-row embedding-test-row">
          <span>
            <strong>Connection</strong>
            <small>
              {item.kind === 'tts'
                ? 'Generates a short sample and verifies the returned audio media.'
                : 'Sends a short silent clip and verifies the transcription request.'}
            </small>
          </span>
          <span className="embedding-test-row__actions">
            <Status value={message ? 'connected' : item.connection_status} />
            <Button
              icon={<PlugZap size={14} />}
              busy={test.isPending}
              onClick={() => test.mutate()}
            >
              Test connection
            </Button>
          </span>
        </div>
        <Feedback
          message={test.error instanceof Error ? test.error.message : message}
          kind={message ? 'success' : 'error'}
        />
        <div className="card__body detail-grid resource-detail-body">
          <Detail label="Name" value={item.name} />
          <Detail label="Location" value={readable(item.location)} />
          <Detail label="Adapter" value={readable(item.adapter || item.provider)} />
          {item.provider_name && <Detail label="Provider" value={item.provider_name} />}
          <Detail label="Language" value={item.language} />
          <Detail label="Model" value={item.model} full />
          {item.voice && <Detail label="Voice" value={item.voice} full />}
          {item.base_url && (
            <Detail
              label={item.provider_base_url_name || 'Connection URL'}
              value={item.base_url}
              full
            />
          )}
          <Detail
            label="Credential"
            value={
              item.provider_api_key_name ||
              (item.api_key_configured ? 'API key saved' : 'No API key selected')
            }
            full
          />
          {options.length > 0 && (
            <Detail
              label="Adapter options"
              value={options.map(([key, value]) => `${readable(key)}: ${String(value)}`).join('\n')}
              full
            />
          )}
          {item.last_error && <Detail label="Last test error" value={item.last_error} full />}
        </div>
      </section>
    </div>
  )
}
