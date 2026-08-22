import { useMutation, useQueryClient } from '@tanstack/react-query'
import { PlugZap } from 'lucide-react'
import { api } from '../../api/client'
import type { EmbeddingModelRecord } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Status } from '../../components/ui/Status'
import { keys } from '../../hooks/useStudioData'
import { readable } from './helpers'

function Detail({ label, value, full = false }: { label: string; value: string; full?: boolean }) {
  return (
    <div className={`detail ${full ? 'detail--full' : ''}`}>
      <dt>{label}</dt>
      <dd>{value || 'Not configured'}</dd>
    </div>
  )
}

export function EmbeddingModelDetails({
  item,
  onTested,
}: {
  item: EmbeddingModelRecord
  onTested: (item: EmbeddingModelRecord) => void
}) {
  const client = useQueryClient()
  const test = useMutation({
    mutationFn: () => api.embeddingModels.test(item.id),
    onSuccess: async (saved) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.embeddingModels }),
        client.invalidateQueries({ queryKey: keys.builtins }),
      ])
      onTested(saved)
    },
  })

  return (
    <div className="stack">
      <section className="card resource-workspace">
        <div className="setting-row embedding-test-row">
          <span>
            <strong>Connection</strong>
            <small>Tests the selected model and records its actual vector dimensions.</small>
          </span>
          <span className="embedding-test-row__actions">
            <Status value={item.connection_status} />
            <Button
              icon={<PlugZap size={14} />}
              busy={test.isPending}
              onClick={() => test.mutate()}
            >
              Test connection
            </Button>
          </span>
        </div>
        <div className="card__body detail-grid resource-detail-body">
          <Detail label="Name" value={item.name} />
          <Detail label="Location" value={readable(item.location)} />
          <Detail label="Connection type" value={readable(item.adapter)} />
          <Detail
            label="Dimensions"
            value={item.dimensions ? `${item.dimensions}` : 'Detected after testing'}
          />
          <Detail label="Model ID" value={item.model} full />
          <Detail label="Base URL" value={item.base_url} full />
          <Detail
            label="Credential"
            value={item.api_key_configured ? 'API key saved' : 'No API key saved'}
            full
          />
          {item.last_error && <Detail label="Last error" value={item.last_error} full />}
        </div>
      </section>
      <Feedback message={test.error instanceof Error ? test.error.message : ''} />
    </div>
  )
}
