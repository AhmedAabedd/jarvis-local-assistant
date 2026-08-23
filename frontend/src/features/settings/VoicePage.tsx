import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Save, X } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { TtsVoiceOption, VoiceSettings } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { keys } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'

type VoiceSection = 'stt' | 'tts'
type ConnectionLocation = 'cloud' | 'local'
type LocalTtsProvider = 'piper' | 'moss_onnx'
type CloudTtsProvider = 'openai_compatible' | 'google'

function LocationPicker({
  name,
  value,
  onChange,
}: {
  name: string
  value: ConnectionLocation
  onChange: (value: ConnectionLocation) => void
}) {
  return (
    <fieldset className="voice-location-picker field--full">
      <legend>Connection location</legend>
      <label>
        <input
          type="radio"
          name={name}
          value="cloud"
          checked={value === 'cloud'}
          onChange={() => onChange('cloud')}
        />
        <span>Cloud</span>
      </label>
      <label>
        <input
          type="radio"
          name={name}
          value="local"
          checked={value === 'local'}
          onChange={() => onChange('local')}
        />
        <span>Local</span>
      </label>
    </fieldset>
  )
}

function SectionPicker({
  value,
  onChange,
}: {
  value: VoiceSection
  onChange: (value: VoiceSection) => void
}) {
  return (
    <div className="model-location-picker" role="tablist" aria-label="Voice configuration">
      <span
        className={`model-location-picker__indicator ${value === 'tts' ? 'is-local' : ''}`}
        aria-hidden="true"
      />
      <button
        type="button"
        role="tab"
        className={value === 'stt' ? 'is-active' : ''}
        aria-selected={value === 'stt'}
        onClick={() => onChange('stt')}
      >
        Speech-to-text
      </button>
      <button
        type="button"
        role="tab"
        className={value === 'tts' ? 'is-active' : ''}
        aria-selected={value === 'tts'}
        onClick={() => onChange('tts')}
      >
        Text-to-speech
      </button>
    </div>
  )
}

export function VoicePage() {
  const client = useQueryClient()
  const query = useQuery({ queryKey: keys.voice, queryFn: api.voice.get })
  const [section, setSection] = useState<VoiceSection>('stt')
  const [sttLocation, setSttLocation] = useState<ConnectionLocation>()
  const [ttsLocation, setTtsLocation] = useState<ConnectionLocation>()
  const [localTtsProvider, setLocalTtsProvider] = useState<LocalTtsProvider>()
  const [cloudTtsProvider, setCloudTtsProvider] = useState<CloudTtsProvider>()
  const [mossModel, setMossModel] = useState<string>()
  const [mossVoices, setMossVoices] = useState<TtsVoiceOption[] | null>(null)
  const [mossVoice, setMossVoice] = useState<string>()
  const [mossChecking, setMossChecking] = useState(false)
  const [mossModelError, setMossModelError] = useState('')
  const [sttMessage, setSttMessage] = useState('')
  const [sttError, setSttError] = useState('')
  const [ttsMessage, setTtsMessage] = useState('')
  const [ttsError, setTtsError] = useState('')
  const [sttDirty, setSttDirty] = useState(false)
  const [ttsDirty, setTtsDirty] = useState(false)
  const [sttFormVersion, setSttFormVersion] = useState(0)
  const [ttsFormVersion, setTtsFormVersion] = useState(0)

  const sttUpdate = useMutation({ mutationFn: api.voice.update })
  const ttsUpdate = useMutation({ mutationFn: api.voice.update })
  const currentTtsProvider = query.data?.tts.provider || ''
  const selectedSttLocation =
    sttLocation ?? (query.data?.stt.provider === 'local_whisper' ? 'local' : 'cloud')
  const selectedTtsLocation =
    ttsLocation ??
    (currentTtsProvider === 'piper' || currentTtsProvider === 'moss_onnx' ? 'local' : 'cloud')
  const selectedLocalTtsProvider =
    localTtsProvider ??
    (currentTtsProvider === 'moss_onnx' ? 'moss_onnx' : ('piper' as LocalTtsProvider))
  const selectedCloudTtsProvider =
    cloudTtsProvider ??
    (currentTtsProvider === 'google' ? 'google' : ('openai_compatible' as CloudTtsProvider))
  const mossModelValue =
    mossModel ?? (currentTtsProvider === 'moss_onnx' ? query.data?.tts.model || '' : '')
  const configuredMossVoice = currentTtsProvider === 'moss_onnx' ? query.data?.tts.voice || '' : ''
  const mossVoiceValue = mossVoice ?? configuredMossVoice
  const mossReady = selectedLocalTtsProvider === 'moss_onnx' && mossVoices !== null
  const selectedMossVoice = mossVoices?.some(
    (voice) => voice.id.toLowerCase() === mossVoiceValue.toLowerCase(),
  )
    ? mossVoiceValue
    : mossVoices?.[0]?.id || ''

  useEffect(() => {
    const model = mossModelValue.trim()
    const shouldDiscover =
      section === 'tts' &&
      selectedTtsLocation === 'local' &&
      selectedLocalTtsProvider === 'moss_onnx' &&
      Boolean(model)
    if (!shouldDiscover) {
      setMossChecking(false)
      setMossModelError('')
      setMossVoices(null)
      return
    }

    let cancelled = false
    setMossChecking(true)
    setMossModelError('')
    setMossVoices(null)
    const timeout = window.setTimeout(async () => {
      try {
        const catalog = await api.voice.voices('moss_onnx', model)
        if (!cancelled) setMossVoices(catalog.voices)
      } catch (error) {
        if (!cancelled) {
          setMossModelError(
            error instanceof Error
              ? error.message
              : 'This is not a supported MOSS model directory.',
          )
        }
      } finally {
        if (!cancelled) setMossChecking(false)
      }
    }, 300)
    return () => {
      cancelled = true
      window.clearTimeout(timeout)
    }
  }, [mossModelValue, section, selectedLocalTtsProvider, selectedTtsLocation])

  if (query.isLoading)
    return (
      <>
        <PageHeader
          title="Voice"
          description="This configuration applies to Mounir voice mode and to voice messages sent to Mounir through Telegram."
        />
        <Loading />
      </>
    )
  if (query.error || !query.data)
    return (
      <>
        <PageHeader
          title="Voice"
          description="This configuration applies to Mounir voice mode and to voice messages sent to Mounir through Telegram."
        />
        <div className="page-content">
          <Feedback
            message={
              query.error instanceof Error
                ? query.error.message
                : 'Voice settings could not be loaded.'
            }
          />
        </div>
      </>
    )

  const settings = query.data as VoiceSettings
  const refreshSettings = async () => {
    await client.invalidateQueries({ queryKey: keys.voice })
  }

  const submitStt = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSttError('')
    setSttMessage('')
    const values = Object.fromEntries(new FormData(event.currentTarget).entries())
    const provider = selectedSttLocation === 'local' ? 'local_whisper' : 'openai_compatible'
    try {
      await sttUpdate.mutateAsync({
        stt: {
          provider,
          model: values.stt_model,
          language: values.stt_language,
          base_url: values.stt_base_url,
          api_key: values.stt_api_key,
        },
      })
      await refreshSettings()
      setSttDirty(false)
      setSttMessage('Speech-to-text settings saved.')
    } catch (error) {
      setSttError(
        error instanceof Error ? error.message : 'Could not save speech-to-text settings.',
      )
    }
  }

  const submitTts = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setTtsError('')
    setTtsMessage('')
    setMossModelError('')
    const form = event.currentTarget
    const values = Object.fromEntries(new FormData(form).entries())
    const provider =
      selectedTtsLocation === 'local' ? selectedLocalTtsProvider : selectedCloudTtsProvider

    if (provider === 'moss_onnx' && !mossReady) {
      if (!mossModelError) {
        setMossModelError('Enter a supported MOSS model directory before saving.')
      }
      return
    }

    try {
      await ttsUpdate.mutateAsync({
        tts: {
          provider,
          model: values.tts_model,
          voice: values.tts_voice,
          language: values.tts_language,
          base_url: values.tts_base_url,
          api_key: values.tts_api_key,
        },
      })
      await refreshSettings()
      setTtsDirty(false)
      setTtsMessage('Text-to-speech settings saved.')
    } catch (error) {
      setTtsError(
        error instanceof Error ? error.message : 'Could not save text-to-speech settings.',
      )
    }
  }

  const updateMossModel = (value: string) => {
    setMossModel(value)
    setMossModelError('')
    setMossVoices(null)
  }

  const discardSttChanges = () => {
    sttUpdate.reset()
    setSttLocation(undefined)
    setSttError('')
    setSttMessage('')
    setSttDirty(false)
    setSttFormVersion((version) => version + 1)
  }

  const discardTtsChanges = () => {
    ttsUpdate.reset()
    setTtsLocation(undefined)
    setLocalTtsProvider(undefined)
    setCloudTtsProvider(undefined)
    setMossModel(undefined)
    setMossVoice(undefined)
    setMossVoices(null)
    setMossModelError('')
    setTtsError('')
    setTtsMessage('')
    setTtsDirty(false)
    setTtsFormVersion((version) => version + 1)
  }

  return (
    <>
      <PageHeader
        title="Voice"
        description="This configuration applies to Mounir voice mode and to voice messages sent to Mounir through Telegram."
      />
      <div className="page-content stack">
        <SectionPicker value={section} onChange={setSection} />

        {section === 'stt' && (
          <Card
            title="Speech-to-text"
            description="Configure how Mounir transcribes incoming voice."
            action={
              sttDirty ? (
                <div className="resource-header-actions">
                  <Button
                    className="resource-header-action"
                    icon={<X size={15} />}
                    disabled={sttUpdate.isPending}
                    onClick={discardSttChanges}
                    aria-label="Discard speech-to-text changes"
                    title="Discard changes"
                  />
                  <Button
                    variant="primary"
                    icon={<Save size={14} />}
                    busy={sttUpdate.isPending}
                    type="submit"
                    form="stt-settings-form"
                  >
                    Save
                  </Button>
                </div>
              ) : undefined
            }
          >
            <form
              key={sttFormVersion}
              id="stt-settings-form"
              className="card__body form-grid"
              onChange={() => setSttDirty(true)}
              onSubmit={submitStt}
            >
              <LocationPicker
                name="stt_location"
                value={selectedSttLocation}
                onChange={setSttLocation}
              />
              {selectedSttLocation === 'local' ? (
                <>
                  <Field
                    key="local-stt-model"
                    full
                    label="Whisper model"
                    hint="Enter a Faster Whisper model name or a compatible local model directory."
                  >
                    <input
                      name="stt_model"
                      defaultValue={
                        settings.stt.provider === 'local_whisper' ? settings.stt.model : 'small'
                      }
                      placeholder="small or full model path"
                      required
                    />
                  </Field>
                  <Field
                    key="cloud-stt-model"
                    full
                    label="Language"
                    hint="Choose Auto-detect for multilingual speech, or force one language."
                  >
                    <select name="stt_language" defaultValue={settings.stt.language || 'auto'}>
                      <option value="auto">Auto-detect</option>
                      <option value="en">English</option>
                      <option value="fr">French</option>
                      <option value="ar">Arabic</option>
                    </select>
                  </Field>
                </>
              ) : (
                <>
                  <Field
                    full
                    label="Model ID"
                    hint="The transcription model ID accepted by the OpenAI-compatible endpoint."
                  >
                    <input
                      name="stt_model"
                      defaultValue={
                        settings.stt.provider === 'openai_compatible' ? settings.stt.model : ''
                      }
                      placeholder="whisper-1 or provider model ID"
                      required
                    />
                  </Field>
                  <Field
                    full
                    label="Language"
                    hint="Choose Auto-detect for multilingual speech, or force one language."
                  >
                    <select name="stt_language" defaultValue={settings.stt.language || 'auto'}>
                      <option value="auto">Auto-detect</option>
                      <option value="en">English</option>
                      <option value="fr">French</option>
                      <option value="ar">Arabic</option>
                    </select>
                  </Field>
                  <Field
                    full
                    label="API URL"
                    hint="An API root or the complete /audio/transcriptions endpoint."
                  >
                    <input
                      type="url"
                      name="stt_base_url"
                      defaultValue={
                        settings.stt.provider === 'openai_compatible' ? settings.stt.base_url : ''
                      }
                      placeholder="https://provider.example/v1"
                      required
                    />
                  </Field>
                  <Field
                    full
                    label="API key"
                    hint={
                      settings.stt.provider === 'openai_compatible' &&
                      settings.stt.api_key_configured
                        ? 'A key is saved. Leave blank to keep it.'
                        : 'Optional for local or unauthenticated compatible endpoints.'
                    }
                  >
                    <input type="password" name="stt_api_key" />
                  </Field>
                </>
              )}
              <div className="field--full">
                <Feedback
                  message={sttError || sttMessage}
                  kind={sttMessage ? 'success' : 'error'}
                />
              </div>
            </form>
          </Card>
        )}

        {section === 'tts' && (
          <Card
            title="Text-to-speech"
            description="Configure how Mounir generates spoken responses."
            action={
              ttsDirty ? (
                <div className="resource-header-actions">
                  <Button
                    className="resource-header-action"
                    icon={<X size={15} />}
                    disabled={ttsUpdate.isPending}
                    onClick={discardTtsChanges}
                    aria-label="Discard text-to-speech changes"
                    title="Discard changes"
                  />
                  <Button
                    variant="primary"
                    icon={<Save size={14} />}
                    busy={ttsUpdate.isPending || mossChecking}
                    type="submit"
                    form="tts-settings-form"
                  >
                    Save
                  </Button>
                </div>
              ) : undefined
            }
          >
            <form
              key={ttsFormVersion}
              id="tts-settings-form"
              className="card__body form-grid"
              onChange={() => setTtsDirty(true)}
              onSubmit={submitTts}
            >
              <LocationPicker
                name="tts_location"
                value={selectedTtsLocation}
                onChange={(value) => {
                  setTtsLocation(value)
                  setTtsError('')
                  setTtsMessage('')
                  setMossModelError('')
                }}
              />
              {selectedTtsLocation === 'local' ? (
                <>
                  <Field
                    full
                    label="Local engine"
                    hint="Choose the local text-to-speech runtime installed on this computer."
                  >
                    <select
                      value={selectedLocalTtsProvider}
                      onChange={(event) => {
                        setLocalTtsProvider(event.target.value as LocalTtsProvider)
                        setTtsError('')
                        setTtsMessage('')
                        setMossModelError('')
                      }}
                    >
                      <option value="piper">Piper</option>
                      <option value="moss_onnx">MOSS TTS Nano ONNX</option>
                    </select>
                  </Field>
                  {selectedLocalTtsProvider === 'piper' ? (
                    <Field
                      key="piper-model"
                      full
                      label="Piper voice model"
                      hint="Full path to a Piper .onnx voice model."
                    >
                      <input
                        name="tts_model"
                        defaultValue={currentTtsProvider === 'piper' ? settings.tts.model : ''}
                        placeholder="/full/path/to/voice.onnx"
                        required
                      />
                    </Field>
                  ) : (
                    <>
                      <Field
                        key="moss-model"
                        full
                        label="MOSS engine directory"
                        hint="Directory containing the compatible MOSS runtime and ONNX models."
                        error={mossModelError}
                      >
                        <input
                          name="tts_model"
                          value={mossModelValue}
                          onChange={(event) => updateMossModel(event.target.value)}
                          placeholder="/full/path/to/MOSS-TTS-Nano"
                          required
                        />
                      </Field>
                      {mossReady && (
                        <Field
                          full
                          label="Voice"
                          hint={
                            mossVoices.length
                              ? `${mossVoices.length} voices discovered from this model package.`
                              : 'This package exposes no voice catalog; enter its supported voice identifier.'
                          }
                        >
                          {mossVoices.length ? (
                            <select
                              name="tts_voice"
                              value={selectedMossVoice}
                              onChange={(event) => setMossVoice(event.target.value)}
                              required
                            >
                              {mossVoices.map((voice) => (
                                <option key={voice.id} value={voice.id}>
                                  {voice.label === voice.id
                                    ? voice.id
                                    : `${voice.id} — ${voice.label}`}
                                  {voice.group ? ` (${voice.group})` : ''}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <input
                              name="tts_voice"
                              value={mossVoiceValue}
                              onChange={(event) => setMossVoice(event.target.value)}
                              placeholder="Voice ID supported by this model"
                              required
                            />
                          )}
                        </Field>
                      )}
                    </>
                  )}
                </>
              ) : (
                <>
                  <Field full label="Cloud provider">
                    <select
                      value={selectedCloudTtsProvider}
                      onChange={(event) =>
                        setCloudTtsProvider(event.target.value as CloudTtsProvider)
                      }
                    >
                      <option value="openai_compatible">OpenAI-compatible API</option>
                      <option value="google">Google Cloud Text-to-Speech</option>
                    </select>
                  </Field>
                  {selectedCloudTtsProvider === 'openai_compatible' ? (
                    <>
                      <Field key="compatible-tts-model" full label="Model ID">
                        <input
                          name="tts_model"
                          defaultValue={
                            currentTtsProvider === 'openai_compatible' ? settings.tts.model : ''
                          }
                          placeholder="tts-1 or provider model ID"
                          required
                        />
                      </Field>
                      <Field
                        key="compatible-tts-voice"
                        full
                        label="Voice"
                        hint="Voice identifier accepted by the endpoint."
                      >
                        <input
                          name="tts_voice"
                          defaultValue={
                            currentTtsProvider === 'openai_compatible' ? settings.tts.voice : ''
                          }
                          placeholder="alloy or provider voice ID"
                          required
                        />
                      </Field>
                    </>
                  ) : (
                    <>
                      <Field key="google-tts-voice" full label="Voice name">
                        <input
                          name="tts_model"
                          defaultValue={currentTtsProvider === 'google' ? settings.tts.model : ''}
                          placeholder="Google voice name"
                          required
                        />
                      </Field>
                      <Field full label="Language code">
                        <input
                          name="tts_language"
                          defaultValue={
                            currentTtsProvider === 'google'
                              ? settings.tts.language || 'en-US'
                              : 'en-US'
                          }
                          placeholder="en-US"
                        />
                      </Field>
                    </>
                  )}
                  <Field key={`${selectedCloudTtsProvider}-api-url`} full label="API URL">
                    <input
                      type="url"
                      name="tts_base_url"
                      defaultValue={
                        currentTtsProvider === selectedCloudTtsProvider
                          ? settings.tts.base_url
                          : selectedCloudTtsProvider === 'google'
                            ? 'https://texttospeech.googleapis.com/v1'
                            : ''
                      }
                      placeholder={
                        selectedCloudTtsProvider === 'google'
                          ? 'https://texttospeech.googleapis.com/v1'
                          : 'https://provider.example/v1'
                      }
                      required
                    />
                  </Field>
                  <Field
                    full
                    label="API key"
                    hint={
                      currentTtsProvider === selectedCloudTtsProvider &&
                      settings.tts.api_key_configured
                        ? 'A key is saved. Leave blank to keep it.'
                        : selectedCloudTtsProvider === 'google'
                          ? 'Required by Google Cloud Text-to-Speech.'
                          : 'Optional for unauthenticated compatible endpoints.'
                    }
                  >
                    <input type="password" name="tts_api_key" />
                  </Field>
                </>
              )}
              <div className="field--full">
                <Feedback
                  message={ttsError || ttsMessage}
                  kind={ttsMessage ? 'success' : 'error'}
                />
              </div>
            </form>
          </Card>
        )}
      </div>
    </>
  )
}
