import { ChevronDown, ChevronUp, RefreshCw, Search } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type {
  ProviderRecord,
  SpeechAdapterOption,
  SpeechAdapterSpec,
  VoiceModelRecord,
} from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { ModelLocationOptions, type ModelLocation } from './ModelLocationOptions'
import { ProviderConnectionFields, type ProviderConnectionValue } from './ProviderConnectionFields'

type VoiceKind = VoiceModelRecord['kind']
type OptionValue = string | number | boolean

function optionDefaults(spec?: SpeechAdapterSpec, saved?: VoiceModelRecord) {
  return Object.fromEntries(
    (spec?.options || []).map((option) => [
      option.key,
      saved?.provider_options?.[option.key] ?? option.default,
    ]),
  ) as Record<string, OptionValue>
}

function OptionField({
  option,
  value,
  onChange,
}: {
  option: SpeechAdapterOption
  value: OptionValue
  onChange: (value: OptionValue) => void
}) {
  if (option.type === 'boolean') {
    return (
      <label className="speech-option-toggle">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>
          <strong>{option.label}</strong>
          {option.hint && <small>{option.hint}</small>}
        </span>
      </label>
    )
  }
  return (
    <Field full label={option.label} hint={option.hint}>
      {option.choices.length ? (
        <select value={String(value)} onChange={(event) => onChange(event.target.value)}>
          {option.choices.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={option.type === 'integer' || option.type === 'number' ? 'number' : 'text'}
          step={option.type === 'number' ? 'any' : undefined}
          value={String(value)}
          onChange={(event) =>
            onChange(
              option.type === 'integer'
                ? Number.parseInt(event.target.value || '0', 10)
                : option.type === 'number'
                  ? Number.parseFloat(event.target.value || '0')
                  : event.target.value,
            )
          }
        />
      )}
    </Field>
  )
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
  const [catalogError, setCatalogError] = useState('')
  const [specs, setSpecs] = useState<SpeechAdapterSpec[]>([])
  const [loadingSpecs, setLoadingSpecs] = useState(true)
  const [location, setLocation] = useState<ModelLocation>(item?.location || 'cloud')
  const [adapter, setAdapter] = useState(item?.adapter || item?.provider || '')
  const [model, setModel] = useState(item?.model || '')
  const [voice, setVoice] = useState(item?.voice || '')
  const [language, setLanguage] = useState(item?.language || 'auto')
  const [options, setOptions] = useState<Record<string, OptionValue>>(item?.provider_options || {})
  const [connection, setConnection] = useState<ProviderConnectionValue>(() => ({
    providerId: item?.provider_id ? String(item.provider_id) : '',
    baseUrlId: item?.provider_base_url_id ? String(item.provider_base_url_id) : '',
    apiKeyId: item?.provider_api_key_id ? String(item.provider_api_key_id) : '',
  }))
  const [modelChoices, setModelChoices] = useState<Array<{ id: string; label: string }>>([])
  const [voiceChoices, setVoiceChoices] = useState<Array<{ id: string; label: string }>>([])
  const [discovering, setDiscovering] = useState<'models' | 'voices' | ''>('')
  const [showAdvanced, setShowAdvanced] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoadingSpecs(true)
    api.voiceModels
      .adapters(kind)
      .then((result) => {
        if (cancelled) return
        setSpecs(result)
        const current = item?.adapter || item?.provider
        const selected = result.find((spec) => spec.id === current)
        const fallback = result.find((spec) => spec.locations.includes(location))
        const next = selected || fallback
        if (next) {
          setAdapter(next.id)
          setOptions(optionDefaults(next, item))
          if (next.language === 'required' && (item?.language || 'auto') === 'auto') {
            setLanguage('')
          }
        }
      })
      .catch((caught) => {
        if (!cancelled)
          setError(
            caught instanceof Error ? caught.message : 'Speech adapters could not be loaded.',
          )
      })
      .finally(() => !cancelled && setLoadingSpecs(false))
    return () => {
      cancelled = true
    }
    // The item is fixed for the lifetime of this keyed modal form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind])

  const availableSpecs = useMemo(
    () => specs.filter((spec) => spec.locations.includes(location)),
    [location, specs],
  )
  const spec = specs.find((candidate) => candidate.id === adapter)

  const chooseLocation = (next: ModelLocation) => {
    setLocation(next)
    const currentStillWorks = spec?.locations.includes(next)
    if (!currentStillWorks) {
      const nextSpec = specs.find((candidate) => candidate.locations.includes(next))
      if (nextSpec) {
        setAdapter(nextSpec.id)
        setOptions(optionDefaults(nextSpec))
        setLanguage(nextSpec.language === 'required' ? '' : 'auto')
      }
    }
    setConnection({ providerId: '', baseUrlId: '', apiKeyId: '' })
    setModelChoices([])
    setVoiceChoices([])
    setCatalogError('')
  }

  const chooseAdapter = (nextId: string) => {
    const next = specs.find((candidate) => candidate.id === nextId)
    setAdapter(nextId)
    setOptions(optionDefaults(next))
    setModelChoices([])
    setVoiceChoices([])
    setVoice('')
    setLanguage(next?.language === 'required' ? '' : 'auto')
    setCatalogError('')
    if (next?.connection === 'none' || next?.connection === 'aws') {
      setConnection({ providerId: '', baseUrlId: '', apiKeyId: '' })
    }
  }

  const discoveryBody = (target: 'models' | 'voices') => ({
    kind,
    target,
    location,
    adapter,
    model,
    voice,
    language,
    provider_options: options,
    provider_id: connection.providerId,
    provider_base_url_id: connection.baseUrlId,
    provider_api_key_id: connection.apiKeyId,
  })

  const discover = async (target: 'models' | 'voices') => {
    setCatalogError('')
    setDiscovering(target)
    try {
      const result = await api.voiceModels.discover(discoveryBody(target))
      if (target === 'models') setModelChoices(result.items)
      else setVoiceChoices(result.items)
      if (!result.items.length)
        setCatalogError(`This adapter returned no ${target}. You can enter an exact ID manually.`)
    } catch (caught) {
      setCatalogError(caught instanceof Error ? caught.message : `Could not discover ${target}.`)
    } finally {
      setDiscovering('')
    }
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const body = Object.fromEntries(new FormData(event.currentTarget).entries())
    try {
      await onSubmit({
        ...body,
        kind,
        location,
        adapter,
        provider: adapter,
        model,
        voice,
        language: spec?.language === 'none' ? 'auto' : language,
        provider_options: options,
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save the speech model.')
    }
  }

  const connected = spec?.connection === 'http' || spec?.connection === 'tcp'
  const basicOptions = spec?.options.filter((option) => !option.advanced) || []
  const advancedOptions = spec?.options.filter((option) => option.advanced) || []

  return (
    <form id={formId} className="form-grid speech-model-form" onSubmit={submit}>
      <ModelLocationOptions value={location} onChange={chooseLocation} />

      <Field
        full
        label={kind === 'tts' ? 'Speech adapter' : 'Transcription adapter'}
        hint="The adapter defines the API or local runtime contract. Your provider, model, and voice remain separate."
      >
        <select
          value={adapter}
          onChange={(event) => chooseAdapter(event.target.value)}
          disabled={loadingSpecs}
          required
        >
          {!adapter && <option value="">Select an adapter</option>}
          {availableSpecs.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.label}
            </option>
          ))}
        </select>
      </Field>

      {spec && (
        <div className="speech-adapter-summary field--full">
          <div>
            <strong>{spec.label}</strong>
            <p>{spec.description}</p>
          </div>
          <div className="chips">
            <span className="chip">{spec.transport}</span>
            {spec.modes.map((mode) => (
              <span className="chip" key={mode}>
                {mode.replaceAll('_', ' ')}
              </span>
            ))}
          </div>
        </div>
      )}

      <Field
        full
        label="Name"
        hint="A recognizable name shown when choosing the active voice pipeline."
      >
        <input name="name" defaultValue={item?.name} required placeholder="Display name" />
      </Field>

      {connected && (
        <ProviderConnectionFields
          providers={providers}
          value={connection}
          onChange={(next) => {
            setConnection(next)
            setModelChoices([])
            setVoiceChoices([])
          }}
          urlLabel={spec?.connection === 'tcp' ? 'Service address' : 'API URL'}
          urlHint={
            spec?.connection === 'tcp'
              ? 'Choose a tcp://host:port Wyoming service address.'
              : 'Choose the provider endpoint or API root. The adapter adds only its documented operation path.'
          }
        />
      )}

      {spec?.connection === 'aws' && (
        <div className="guidance field--full">
          AWS speech adapters use the standard AWS credential chain. Configure environment
          credentials, a shared AWS profile, or an instance role before testing this model.
        </div>
      )}

      {spec && (
        <Field
          full
          label={spec.model_label}
          hint="Enter the exact provider model ID or local package path. Discovery is used only when the adapter offers it."
        >
          <div className="speech-discovery-field">
            {modelChoices.length ? (
              <select
                value={model}
                onChange={(event) => setModel(event.target.value)}
                required={spec.model_required}
              >
                <option value="">Select a model</option>
                {modelChoices.map((choice) => (
                  <option key={choice.id} value={choice.id}>
                    {choice.label === choice.id ? choice.id : `${choice.label} — ${choice.id}`}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={model}
                onChange={(event) => setModel(event.target.value)}
                required={spec.model_required}
                placeholder="Exact model ID or path"
              />
            )}
            {spec.discovery.includes('models') && (
              <Button
                type="button"
                icon={
                  discovering === 'models' ? (
                    <RefreshCw className="spin" size={14} />
                  ) : (
                    <Search size={14} />
                  )
                }
                onClick={() => void discover('models')}
                disabled={Boolean(discovering)}
              >
                Discover
              </Button>
            )}
          </div>
        </Field>
      )}

      {spec && spec.voice !== 'none' && (
        <Field
          full
          label="Voice"
          hint={
            spec.voice === 'required'
              ? 'This adapter requires an exact voice identifier.'
              : 'Optional. Leave blank only when the provider supports a default voice.'
          }
        >
          <div className="speech-discovery-field">
            {voiceChoices.length ? (
              <select
                value={voice}
                onChange={(event) => setVoice(event.target.value)}
                required={spec.voice === 'required'}
              >
                <option value="">
                  {spec.voice === 'required' ? 'Select a voice' : 'Provider default'}
                </option>
                {voiceChoices.map((choice) => (
                  <option key={choice.id} value={choice.id}>
                    {choice.label === choice.id ? choice.id : `${choice.label} — ${choice.id}`}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={voice}
                onChange={(event) => setVoice(event.target.value)}
                required={spec.voice === 'required'}
                placeholder={spec.voice === 'required' ? 'Voice ID' : 'Optional voice ID'}
              />
            )}
            {spec.discovery.includes('voices') && (
              <Button
                type="button"
                icon={
                  discovering === 'voices' ? (
                    <RefreshCw className="spin" size={14} />
                  ) : (
                    <Search size={14} />
                  )
                }
                onClick={() => void discover('voices')}
                disabled={Boolean(discovering) || (!model.trim() && adapter === 'moss_onnx')}
              >
                Discover
              </Button>
            )}
          </div>
        </Field>
      )}

      {spec && spec.language !== 'none' && (
        <Field
          full
          label="Language"
          hint={
            spec.language === 'required'
              ? 'This adapter needs an explicit language or locale code.'
              : 'Use auto when the selected model supports automatic language detection.'
          }
        >
          <input
            value={language}
            onChange={(event) => setLanguage(event.target.value)}
            required
            placeholder={spec.language === 'required' ? 'BCP-47 language code' : 'auto'}
          />
        </Field>
      )}

      {basicOptions.map((option) => (
        <OptionField
          key={option.key}
          option={option}
          value={options[option.key] ?? option.default}
          onChange={(value) => setOptions((current) => ({ ...current, [option.key]: value }))}
        />
      ))}

      {advancedOptions.length > 0 && (
        <div className="speech-advanced field--full">
          <button
            type="button"
            onClick={() => setShowAdvanced((current) => !current)}
            aria-expanded={showAdvanced}
          >
            <span>
              <strong>Advanced adapter options</strong>
              <small>Provider-specific controls kept with this model.</small>
            </span>
            {showAdvanced ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </button>
          {showAdvanced && (
            <div className="speech-advanced__fields form-grid">
              {advancedOptions.map((option) => (
                <OptionField
                  key={option.key}
                  option={option}
                  value={options[option.key] ?? option.default}
                  onChange={(value) =>
                    setOptions((current) => ({ ...current, [option.key]: value }))
                  }
                />
              ))}
            </div>
          )}
        </div>
      )}

      <div className="field--full">
        <Feedback message={catalogError || error} />
      </div>
    </form>
  )
}
