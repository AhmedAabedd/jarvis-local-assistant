import { Search } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { EmbeddingModelRecord } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Field } from '../../components/ui/Field'
import { Feedback } from '../../components/ui/Feedback'

type Location = EmbeddingModelRecord['location']
type Adapter = EmbeddingModelRecord['adapter']

export function EmbeddingModelForm({
  item,
  formId,
  onSubmit,
}: {
  item?: EmbeddingModelRecord
  formId: string
  onSubmit: (body: object) => Promise<void>
}) {
  const [error, setError] = useState('')
  const [discoveryError, setDiscoveryError] = useState('')
  const [discovering, setDiscovering] = useState(false)
  const [models, setModels] = useState<string[]>([])
  const [location, setLocation] = useState<Location>(item?.location || 'cloud')
  const [adapter, setAdapter] = useState<Adapter>(item?.adapter || 'openai_compatible')
  const [baseUrl, setBaseUrl] = useState(
    item?.base_url || (location === 'local' ? 'http://localhost:11434/v1' : ''),
  )
  const [apiKey, setApiKey] = useState('')

  const changeLocation = (next: Location) => {
    setLocation(next)
    if (!item && !baseUrl) {
      setBaseUrl(next === 'local' ? 'http://localhost:11434/v1' : '')
    }
  }

  const discover = async () => {
    setDiscoveryError('')
    setDiscovering(true)
    try {
      const result = await api.embeddingModels.discover({
        ...(item ? { id: item.id } : {}),
        location,
        adapter,
        base_url: baseUrl,
        api_key: apiKey,
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
    if (item && !String(body.api_key || '').trim()) delete body.api_key
    try {
      await onSubmit(body)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save the embedding model.')
    }
  }

  return (
    <form id={formId} className="form-grid" onSubmit={submit}>
      <input type="hidden" name="location" value={location} />
      <div className="model-location-picker" role="group" aria-label="Embedding model location">
        <span
          className={`model-location-picker__indicator ${location === 'local' ? 'is-local' : ''}`}
          aria-hidden="true"
        />
        <button
          type="button"
          className={location === 'cloud' ? 'is-active' : ''}
          aria-pressed={location === 'cloud'}
          onClick={() => changeLocation('cloud')}
        >
          Cloud
        </button>
        <button
          type="button"
          className={location === 'local' ? 'is-active' : ''}
          aria-pressed={location === 'local'}
          onClick={() => changeLocation('local')}
        >
          Local
        </button>
      </div>
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
      <Field
        full
        label="Base URL"
        hint={
          adapter === 'ollama'
            ? 'The Ollama server address. The standard local API is http://localhost:11434/v1.'
            : 'The OpenAI-compatible API root. Do not include /embeddings.'
        }
      >
        <input
          type="url"
          name="base_url"
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          placeholder={
            location === 'local' ? 'http://localhost:11434/v1' : 'https://provider.example/v1'
          }
          required
        />
      </Field>
      <Field
        full
        label="API key"
        hint={
          item?.api_key_configured
            ? 'A key is saved. Leave blank to keep it.'
            : location === 'local'
              ? 'Leave blank unless the local service requires authentication.'
              : 'Enter the provider key, or an environment reference such as $EMBEDDING_API_KEY.'
        }
      >
        <input
          name="api_key"
          type="password"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          placeholder="Enter an API key"
        />
      </Field>
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
