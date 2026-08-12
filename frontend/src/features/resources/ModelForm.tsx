import { useState, type FormEvent } from 'react'
import type { ModelRecord } from '../../api/types'
import { Field } from '../../components/ui/Field'
import { Feedback } from '../../components/ui/Feedback'

export function ModelForm({
  item,
  formId,
  onSubmit,
}: {
  item?: ModelRecord
  formId: string
  onSubmit: (body: object) => Promise<void>
}) {
  const [error, setError] = useState('')
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
      <div className="guidance">
        <strong>OpenAI-compatible models:</strong> Connect any local or hosted provider exposing a
        compatible chat-completions endpoint. Models used by agents should support tool calling.
      </div>
      <Field full label="Name" hint="A recognizable name shown throughout Agent Studio.">
        <input name="name" defaultValue={item?.name} required placeholder="Display name" />
      </Field>
      <Field full label="Model ID" hint="The exact model identifier expected by the provider.">
        <input name="model" defaultValue={item?.model} required placeholder="Model name or ID" />
      </Field>
      <Field
        full
        label="Provider"
        hint="A display label for the service providing this OpenAI-compatible model."
      >
        <input
          name="provider"
          defaultValue={item?.provider}
          placeholder="OpenAI, Gemini, NVIDIA, Ollama…"
        />
      </Field>
      <Field
        full
        label="Base URL"
        hint="The OpenAI-compatible API root. Do not include /chat/completions."
      >
        <input
          name="base_url"
          defaultValue={item?.base_url}
          placeholder="https://provider.example/v1"
          required
        />
      </Field>
      <Field
        full
        label="API key"
        hint={
          item?.api_key_configured || item?.api_key
            ? 'A key is saved. Leave blank to keep it.'
            : 'Usually optional for local models.'
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
