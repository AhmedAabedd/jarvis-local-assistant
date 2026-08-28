import type { VoiceModelRecord } from '../../api/types'
import { readable } from './helpers'

function Detail({ label, value, full = false }: { label: string; value?: string; full?: boolean }) {
  return (
    <div className={`detail ${full ? 'detail--full' : ''}`}>
      <dt>{label}</dt>
      <dd>{value || 'Not configured'}</dd>
    </div>
  )
}

export function VoiceModelDetails({ item }: { item: VoiceModelRecord }) {
  return (
    <section className="card resource-workspace">
      <div className="card__body detail-grid resource-detail-body">
        <Detail label="Name" value={item.name} />
        <Detail label="Location" value={readable(item.location)} />
        <Detail label="Engine" value={readable(item.provider)} />
        <Detail label="Language" value={item.language} />
        <Detail
          label={item.provider === 'google' ? 'Voice name' : 'Model'}
          value={item.model}
          full
        />
        {item.voice && <Detail label="Voice" value={item.voice} full />}
        {item.base_url && <Detail label="Base URL" value={item.base_url} full />}
        <Detail
          label="Credential"
          value={item.api_key_configured ? 'API key saved' : 'No API key saved'}
          full
        />
      </div>
    </section>
  )
}
