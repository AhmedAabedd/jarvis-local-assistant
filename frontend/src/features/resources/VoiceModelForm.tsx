import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { ProviderRecord, TtsVoiceOption, VoiceModelRecord } from '../../api/types'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { ModelLocationOptions, type ModelLocation } from './ModelLocationOptions'
import { ProviderConnectionFields, type ProviderConnectionValue } from './ProviderConnectionFields'

type VoiceKind = VoiceModelRecord['kind']

function defaultProvider(kind: VoiceKind, location: ModelLocation) {
  if (kind === 'stt') return location === 'local' ? 'local_whisper' : 'openai_compatible'
  return location === 'local' ? 'piper' : 'openai_compatible'
}

export function VoiceModelForm({
  kind,
  item,
  providers,
  formId,
  onSubmit,
}: {
  kind: VoiceKind
  item?: VoiceModelRecord
  providers: ProviderRecord[]
  formId: string
  onSubmit: (body: object) => Promise<void>
}) {
  const [error, setError] = useState('')
  const [location, setLocation] = useState<ModelLocation>(item?.location || 'cloud')
  const [provider, setProvider] = useState(item?.provider || defaultProvider(kind, location))
  const [model, setModel] = useState(item?.model || '')
  const [voice, setVoice] = useState(item?.voice || '')
  const [connection, setConnection] = useState<ProviderConnectionValue>(() => ({
    providerId: item?.provider_id ? String(item.provider_id) : '',
    baseUrlId: item?.provider_base_url_id ? String(item.provider_base_url_id) : '',
    apiKeyId: item?.provider_api_key_id ? String(item.provider_api_key_id) : '',
  }))
  const [voices, setVoices] = useState<TtsVoiceOption[] | null>(null)
  const [voiceError, setVoiceError] = useState('')

  const selectLocation = (next: ModelLocation) => {
    setLocation(next)
    const nextProvider = defaultProvider(kind, next)
    setProvider(nextProvider)
    setConnection({ providerId: '', baseUrlId: '', apiKeyId: '' })
    setVoice('')
    setVoices(null)
    setVoiceError('')
  }

  useEffect(() => {
    if (kind !== 'tts' || provider !== 'moss_onnx' || !model.trim()) {
      setVoices(null)
      setVoiceError('')
      return
    }
    let cancelled = false
    const timeout = window.setTimeout(async () => {
      try {
        const catalog = await api.voice.voices(provider, model.trim())
        if (!cancelled) {
          setVoices(catalog.voices)
          if (catalog.voices.length && !catalog.voices.some((option) => option.id === voice)) {
            setVoice(catalog.voices[0].id)
          }
        }
      } catch (caught) {
        if (!cancelled) {
          setVoices(null)
          setVoiceError(
            caught instanceof Error ? caught.message : 'Could not inspect this model package.',
          )
        }
      }
    }, 300)
    return () => {
      cancelled = true
      window.clearTimeout(timeout)
    }
  }, [kind, model, provider, voice])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const body = Object.fromEntries(new FormData(event.currentTarget).entries())
    try {
      await onSubmit({ ...body, kind, location, provider })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save the voice model.')
    }
  }

  const remote = location === 'cloud'
  const isTts = kind === 'tts'
  const needsVoice = isTts && (provider === 'openai_compatible' || provider === 'moss_onnx')

  return (
    <form id={formId} className="form-grid" onSubmit={submit}>
      <ModelLocationOptions value={location} onChange={selectLocation} />

      <div className="guidance">
        Save this {isTts ? 'speech' : 'transcription'} configuration once, then select it from the
        Voice page. Provider-specific options remain isolated to this model.
      </div>

      <Field full label="Name" hint="A recognizable name shown in model selections.">
        <input name="name" defaultValue={item?.name} required placeholder="Display name" />
      </Field>

      {isTts && (
        <Field full label={remote ? 'Cloud provider' : 'Local engine'}>
          <select
            value={provider}
            onChange={(event) => {
              const next = event.target.value
              setProvider(next)
              setVoice('')
              setVoices(null)
            }}
          >
            {remote ? (
              <>
                <option value="openai_compatible">OpenAI-compatible API</option>
                <option value="google">Google Cloud Text-to-Speech</option>
              </>
            ) : (
              <>
                <option value="piper">Piper</option>
                <option value="moss_onnx">MOSS TTS Nano ONNX</option>
              </>
            )}
          </select>
        </Field>
      )}

      <Field
        full
        label={
          provider === 'piper'
            ? 'Piper voice model'
            : provider === 'moss_onnx'
              ? 'MOSS engine directory'
              : provider === 'google'
                ? 'Voice name'
                : 'Model ID'
        }
        hint={
          provider === 'piper' || provider === 'moss_onnx'
            ? 'Enter the full local model path.'
            : 'Enter the exact identifier accepted by the provider.'
        }
        error={provider === 'moss_onnx' ? voiceError : ''}
      >
        <input
          name="model"
          value={model}
          onChange={(event) => setModel(event.target.value)}
          required
          placeholder={
            provider === 'piper'
              ? '/full/path/to/voice.onnx'
              : provider === 'moss_onnx'
                ? '/full/path/to/MOSS-TTS-Nano'
                : 'Model name or ID'
          }
        />
      </Field>

      {needsVoice && (
        <Field
          full
          label="Voice"
          hint={
            provider === 'moss_onnx'
              ? 'Voices are discovered from the selected model package when available.'
              : 'The voice identifier accepted by the endpoint.'
          }
        >
          {provider === 'moss_onnx' && voices?.length ? (
            <select name="voice" value={voice} onChange={(event) => setVoice(event.target.value)}>
              {voices.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label === option.id ? option.id : `${option.id} — ${option.label}`}
                </option>
              ))}
            </select>
          ) : (
            <input
              name="voice"
              value={voice}
              onChange={(event) => setVoice(event.target.value)}
              required
              placeholder="Voice ID"
            />
          )}
        </Field>
      )}

      {kind === 'stt' && (
        <Field
          full
          label="Language"
          hint="Use auto, or enter the language code supported by your model or provider."
        >
          <input
            name="language"
            defaultValue={item?.language || 'auto'}
            list={`${formId}-stt-languages`}
            placeholder="auto or a language code"
            required
          />
          <datalist id={`${formId}-stt-languages`}>
            <option value="auto">Auto-detect</option>
            <option value="en">English</option>
            <option value="fr">French</option>
            <option value="ar">Arabic</option>
          </datalist>
        </Field>
      )}
      {provider === 'google' && (
        <Field full label="Language code" hint="The BCP 47 code used by this voice.">
          <input name="language" defaultValue={item?.language || 'en-US'} placeholder="en-US" />
        </Field>
      )}
      {isTts && provider !== 'google' && <input type="hidden" name="language" value="auto" />}

      {remote && (
        <ProviderConnectionFields
          providers={providers}
          value={connection}
          onChange={setConnection}
          urlLabel="API URL"
          urlHint={
            kind === 'stt'
              ? 'Choose an API root or complete /audio/transcriptions endpoint.'
              : 'Choose an API root or complete /audio/speech endpoint.'
          }
        />
      )}
      <div className="field--full">
        <Feedback message={error} />
      </div>
    </form>
  )
}
