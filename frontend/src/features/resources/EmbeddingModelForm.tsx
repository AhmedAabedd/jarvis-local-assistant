import { Search } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { EmbeddingModelRecord, ProviderRecord } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Field } from '../../components/ui/Field'
import { Feedback } from '../../components/ui/Feedback'
import { ModelLocationOptions, type ModelLocation } from './ModelLocationOptions'
import { ProviderConnectionFields, type ProviderConnectionValue } from './ProviderConnectionFields'

type Adapter = EmbeddingModelRecord['adapter']

export function EmbeddingModelForm({
  item,
  providers,
  formId,
  onSubmit,
}: {
  item?: EmbeddingModelRecord
  providers: ProviderRecord[]
  formId: string
  onSubmit: (body: object) => Promise<void>
}) {
  const [error, setError] = useState('')
  const [discoveryError, setDiscoveryError] = useState('')
  const [discovering, setDiscovering] = useState(false)
  const [models, setModels] = useState<string[]>([])
  const [location, setLocation] = useState<ModelLocation>(item?.location || 'cloud')
  const [adapter, setAdapter] = useState<Adapter>(item?.adapter || 'openai_compatible')
  const [connection, setConnection] = useState<ProviderConnectionValue>(() => ({
    providerId: item?.provider_id ? String(item.provider_id) : '',
    baseUrlId: item?.provider_base_url_id ? String(item.provider_base_url_id) : '',
    apiKeyId: item?.provider_api_key_id ? String(item.provider_api_key_id) : '',
  }))

  const changeLocation = (next: ModelLocation) => {
    setLocation(next)
  }

  const discover = async () => {
    setDiscoveryError('')
    setDiscovering(true)
    try {
      const result = await api.embeddingModels.discover({
        ...(item ? { id: item.id } : {}),
        location,
        adapter,
        provider_id: connection.providerId,
        provider_base_url_id: connection.baseUrlId,
        provider_api_key_id: connection.apiKeyId,
      })
      setModels(result.models)
      if (!result.models.length) {
        setDiscoveryError('The connection returned no models. You can still enter a model ID.')
      }
    } catch (caught) {
      setDiscoveryError(caught instanceof Error ? caught.message : 'Could not discover models.')
    } finally {
      setDiscovering(false)
    }
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const body = Object.fromEntries(new FormData(event.currentTarget).entries())
    try {
      await onSubmit(body)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save the embedding model.')
    }
  }

  return (
    <form id={formId} className="form-grid" onSubmit={submit}>
      <ModelLocationOptions value={location} onChange={changeLocation} />
      <div className="guidance">
        Save a reusable embedding connection, then test it so Mounir can detect the vector
        dimensions required by GBrain.
      </div>
      <Field full label="Name" hint="A recognizable name shown in model selections.">
        <input name="name" defaultValue={item?.name} required placeholder="Display name" />
      </Field>
      <Field
        full
        label="Connection type"
        hint="Use the widely supported OpenAI embedding API, or Ollama's native local API."
      >
        <select
          name="adapter"
          value={adapter}
          onChange={(event) => setAdapter(event.target.value as Adapter)}
        >
          <option value="openai_compatible">OpenAI-compatible</option>
          <option value="ollama">Ollama</option>
        </select>
      </Field>
      <ProviderConnectionFields
        providers={providers}
        value={connection}
        onChange={setConnection}
        urlHint={
          adapter === 'ollama'
            ? 'Choose the saved Ollama-compatible server root.'
            : 'Choose an OpenAI-compatible API root; do not include /embeddings.'
        }
      />
      <div className="field field--full">
        <span className="field__label">Model ID</span>
        <span className="field__hint">
          Enter the exact identifier, or discover models advertised by the service. Some providers
          return non-embedding models too; the connection test verifies your choice.
        </span>
        <div className="embedding-model-picker">
          <input
            name="model"
            defaultValue={item?.model}
            list={`${formId}-models`}
            required
            placeholder="Model name or ID"
          />
          <Button type="button" icon={<Search size={14} />} busy={discovering} onClick={discover}>
            Discover
          </Button>
        </div>
        <datalist id={`${formId}-models`}>
          {models.map((model) => (
            <option key={model} value={model} />
          ))}
        </datalist>
        {discoveryError && <span className="field__error">{discoveryError}</span>}
      </div>
      <div className="field--full">
        <Feedback message={error} />
      </div>
    </form>
  )
}
