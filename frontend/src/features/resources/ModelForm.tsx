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
        <strong>Compatibility:</strong> Ollama models can power the supervisor and MCP subagents.
        Other OpenAI-compatible endpoints can power MCP subagents when tool calling is supported.
      </div>
      <Field full label="Name" hint="A recognizable name shown throughout Agent Studio.">
        <input name="name" defaultValue={item?.name} required placeholder="Display name" />
      </Field>
      <Field full label="Model ID" hint="The exact model identifier expected by the provider.">
        <input name="model" defaultValue={item?.model} required placeholder="Model name or ID" />
      </Field>
      <Field full label="Provider" hint="Used to match models to compatible built-in agents.">
        <input name="provider" defaultValue={item?.provider} placeholder="Ollama, Groq, Mistral…" />
      </Field>
      <Field
        full
        label="Base URL"
        hint="OpenAI-compatible API address. Ollama normally uses http://localhost:11434/v1."
      >
        <input
          name="base_url"
          defaultValue={item?.base_url}
          required
          placeholder="https://provider.example/v1"
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
