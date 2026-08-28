import { useState, type FormEvent } from 'react'
import type { ModelRecord, ProviderRecord } from '../../api/types'
import { Field } from '../../components/ui/Field'
import { Feedback } from '../../components/ui/Feedback'
import { ModelLocationOptions, type ModelLocation } from './ModelLocationOptions'
import { ProviderConnectionFields, type ProviderConnectionValue } from './ProviderConnectionFields'

export function ModelForm({
  item,
  providers,
  formId,
  onSubmit,
}: {
  item?: ModelRecord
  providers: ProviderRecord[]
  formId: string
  onSubmit: (body: object) => Promise<void>
}) {
  const [error, setError] = useState('')
  const [location, setLocation] = useState<ModelLocation>(item?.location || 'cloud')
  const [connection, setConnection] = useState<ProviderConnectionValue>(() => ({
    providerId: item?.provider_id ? String(item.provider_id) : '',
    baseUrlId: item?.provider_base_url_id ? String(item.provider_base_url_id) : '',
    apiKeyId: item?.provider_api_key_id ? String(item.provider_api_key_id) : '',
  }))

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const body = Object.fromEntries(new FormData(event.currentTarget).entries())
    try {
      await onSubmit(body)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save the model.')
    }
  }

  return (
    <form id={formId} className="form-grid" onSubmit={submit}>
      <ModelLocationOptions value={location} onChange={setLocation} />
      <div className="guidance">
        {location === 'cloud' ? (
          <>
            <strong>Cloud LLM:</strong> Select a saved provider endpoint and credential. Models used
            by agents should support the OpenAI-compatible chat and tool-calling contract.
          </>
        ) : (
          <>
            <strong>Local LLM:</strong> Select a provider configured for Ollama, LM Studio, LocalAI,
            vLLM, or another local OpenAI-compatible runtime.
          </>
        )}
      </div>
      <Field full label="Name" hint="A recognizable name shown throughout Agent Studio.">
        <input name="name" defaultValue={item?.name} required placeholder="Display name" />
      </Field>
      <Field full label="Model ID" hint="The exact model identifier expected by the provider.">
        <input name="model" defaultValue={item?.model} required placeholder="Model name or ID" />
      </Field>
      <ProviderConnectionFields providers={providers} value={connection} onChange={setConnection} />
      <div className="field--full">
        <Feedback message={error} />
      </div>
    </form>
  )
}
