import { useState, type FormEvent } from 'react'
import type { ModelRecord } from '../../api/types'
import { Field } from '../../components/ui/Field'
import { Feedback } from '../../components/ui/Feedback'
import { ModelLocationOptions, type ModelLocation } from './ModelLocationOptions'

const LOCAL_PROVIDER_NAMES = ['local', 'ollama', 'lm studio', 'vllm', 'llama.cpp']

function inferLocation(item?: ModelRecord): ModelLocation {
  if (!item) return 'cloud'
  if (item.location === 'cloud' || item.location === 'local') return item.location
  const provider = String(item.provider || '').toLowerCase()
  if (LOCAL_PROVIDER_NAMES.some((name) => provider.includes(name))) return 'local'
  try {
    const hostname = new URL(item.base_url).hostname
    if (['localhost', '127.0.0.1', '0.0.0.0', '::1'].includes(hostname)) return 'local'
  } catch {
    // Keep an invalid legacy URL editable as a cloud connection.
  }
  return 'cloud'
}

export function ModelForm({
  item,
  formId,
  onSubmit,
}: {
  item?: ModelRecord
  formId: string
  onSubmit: (body: object) => Promise<void>
}) {
  const initialLocation = inferLocation(item)
  const [error, setError] = useState('')
  const [location, setLocation] = useState<ModelLocation>(initialLocation)
  const [connections, setConnections] = useState(() => ({
    cloud: {
      provider: initialLocation === 'cloud' ? item?.provider || '' : '',
      baseUrl: initialLocation === 'cloud' ? item?.base_url || '' : '',
    },
    local: {
      provider: initialLocation === 'local' ? item?.provider || '' : 'Ollama',
      baseUrl: initialLocation === 'local' ? item?.base_url || '' : 'http://localhost:11434/v1',
    },
  }))
  const connection = connections[location]
  const updateConnection = (field: 'provider' | 'baseUrl', value: string) =>
    setConnections((current) => ({
      ...current,
      [location]: { ...current[location], [field]: value },
    }))
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const body = Object.fromEntries(new FormData(event.currentTarget).entries())
    if (item && !String(body.api_key || '').trim()) delete body.api_key
    try {
      await onSubmit(body)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the model.')
    }
  }
  return (
    <form id={formId} className="form-grid" onSubmit={submit}>
      <ModelLocationOptions value={location} onChange={setLocation} />
      <div className="guidance">
        {location === 'cloud' ? (
          <>
            <strong>Cloud LLM:</strong> Enter the model ID and OpenAI-compatible API root supplied
            by your hosted provider. Models used by agents should support tool calling.
          </>
        ) : (
          <>
            <strong>Local LLM:</strong> Connect Ollama, LM Studio, LocalAI, vLLM, or another local
            OpenAI-compatible runtime. The Ollama defaults below work with its standard local
            server; an API key is normally unnecessary.
          </>
        )}
      </div>
      <Field full label="Name" hint="A recognizable name shown throughout Agent Studio.">
        <input name="name" defaultValue={item?.name} required placeholder="Display name" />
      </Field>
      <Field full label="Model ID" hint="The exact model identifier expected by the provider.">
        <input name="model" defaultValue={item?.model} required placeholder="Model name or ID" />
      </Field>
      <Field
        full
        label={location === 'local' ? 'Local runtime' : 'Provider'}
        hint={
          location === 'local'
            ? 'A display label such as Ollama, LM Studio, LocalAI, or vLLM.'
            : 'A display label for the hosted service providing this model.'
        }
      >
        <input
          name="provider"
          value={connection.provider}
          onChange={(event) => updateConnection('provider', event.target.value)}
          placeholder={location === 'local' ? 'Ollama' : 'OpenAI, Gemini, NVIDIA…'}
        />
      </Field>
      <Field
        full
        label="Base URL"
        hint={
          location === 'local'
            ? 'The local OpenAI-compatible API root. Ollama uses http://localhost:11434/v1.'
            : 'The hosted OpenAI-compatible API root. Do not include /chat/completions.'
        }
      >
        <input
          type="url"
          name="base_url"
          value={connection.baseUrl}
          onChange={(event) => updateConnection('baseUrl', event.target.value)}
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
          item?.api_key_configured || item?.api_key
            ? 'A key is saved. Leave blank to keep it.'
            : location === 'local'
              ? 'Leave blank unless your local runtime requires authentication.'
              : 'Enter the bearer key required by the hosted provider, if any.'
        }
      >
        <input name="api_key" type="password" placeholder="Enter a new API key" />
      </Field>
      <div className="field--full">
        <Feedback message={error} />
      </div>
    </form>
  )
}
