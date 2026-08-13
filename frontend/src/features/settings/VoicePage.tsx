import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Save } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { keys } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'

export function VoicePage() {
  const client = useQueryClient(),
    query = useQuery({ queryKey: keys.voice, queryFn: api.voice.get }),
    [stt, setStt] = useState<string>(),
    [tts, setTts] = useState<string>(),
    [success, setSuccess] = useState('')
  const update = useMutation({
    mutationFn: api.voice.update,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: keys.voice })
      setSuccess('Voice settings saved.')
    },
  })
  if (query.isLoading)
    return (
      <>
        <PageHeader title="Voice" description="Configure speech recognition and voice output" />
        <Loading />
      </>
    )
  const sttProvider = stt || query.data?.stt.provider || 'local_whisper',
    ttsProvider = tts || query.data?.tts.provider || 'piper',
    remoteStt = sttProvider === 'openai_compatible',
    compatibleTts = ttsProvider === 'openai_compatible',
    googleTts = ttsProvider === 'google'
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setSuccess('')
    const values = Object.fromEntries(new FormData(e.currentTarget).entries())
    update.mutate({
      stt: {
        provider: values.stt_provider,
        model: values.stt_model,
        language: values.stt_language,
        base_url: values.stt_base_url,
        api_key: values.stt_api_key,
      },
      tts: {
        provider: values.tts_provider,
        model: values.tts_model,
        voice: values.tts_voice,
        language: values.tts_language,
        base_url: values.tts_base_url,
        api_key: values.tts_api_key,
      },
    })
  }
  return (
    <>
      <PageHeader
        title="Voice"
        description="Configure speech recognition and voice output across Mounir"
      />
      <div className="page-content">
        <form className="stack" onSubmit={submit}>
          <p className="voice-settings-note">
            This configuration applies to Mounir voice mode and to voice messages sent to
            Mounir through Telegram.
            <br />
            Telegram uses it only for voice input, while Mounir still replies with text.
          </p>
          <div className="settings-grid">
            <Card title="Speech-to-text" description="Choose how voice messages are transcribed.">
              <div className="card__body form-grid">
                <div className="guidance field--full">
                  Choose Faster Whisper for a supported local model, or connect a hosted or local
                  service exposing the OpenAI-compatible <code>/audio/transcriptions</code> API.
                </div>
                <Field full label="Connection type">
                  <select
                    name="stt_provider"
                    value={sttProvider}
                    onChange={(e) => setStt(e.target.value)}
                  >
                    <option value="local_whisper">Faster Whisper (local)</option>
                    <option value="openai_compatible">
                      OpenAI-compatible API (hosted or local)
                    </option>
                  </select>
                </Field>
                <Field
                  full
                  label={remoteStt ? 'Model ID' : 'Model name or path'}
                  hint={
                    remoteStt
                      ? 'The exact transcription model ID accepted by your endpoint.'
                      : 'Enter the name or full directory path of a model supported by Faster Whisper.'
                  }
                >
                  <input
                    name="stt_model"
                    defaultValue={query.data?.stt.model}
                    placeholder={remoteStt ? 'whisper-1 or provider model ID' : 'Model name or path'}
                    required
                  />
                </Field>
                <Field
                  full
                  label="Language"
                  hint="Use auto for detection, or a language code such as en, fr, or ar."
                >
                  <input name="stt_language" defaultValue={query.data?.stt.language || 'auto'} />
                </Field>
                {remoteStt && (
                  <>
                    <Field
                      full
                      label="API URL"
                      hint="An API root or the complete /audio/transcriptions endpoint."
                    >
                      <input
                        type="url"
                        name="stt_base_url"
                        defaultValue={query.data?.stt.base_url}
                        placeholder="https://provider.example/v1"
                        required
                      />
                    </Field>
                    <Field
                      full
                      label="API key"
                      hint={
                        query.data?.stt.api_key_configured
                          ? 'A key is saved. Leave blank to keep it.'
                          : 'Optional for local or unauthenticated endpoints.'
                      }
                    >
                      <input type="password" name="stt_api_key" />
                    </Field>
                  </>
                )}
              </div>
            </Card>
            <Card title="Text-to-speech" description="Choose how replies are converted to audio.">
              <div className="card__body form-grid">
                <div className="guidance field--full">
                  Connect any hosted or local service exposing the OpenAI-compatible{' '}
                  <code>/audio/speech</code> API. Piper remains available for fully offline speech.
                </div>
                <Field full label="Connection type">
                  <select
                    name="tts_provider"
                    value={ttsProvider}
                    onChange={(e) => setTts(e.target.value)}
                  >
                    <option value="piper">Piper (local)</option>
                    <option value="openai_compatible">
                      OpenAI-compatible API (hosted or local)
                    </option>
                    <option value="google">Google Cloud Text-to-Speech</option>
                  </select>
                </Field>
                <Field
                  full
                  label={googleTts ? 'Voice name' : compatibleTts ? 'Model ID' : 'Voice model path'}
                  hint={
                    compatibleTts
                      ? 'The exact speech model ID accepted by your endpoint.'
                      : googleTts
                        ? 'The exact voice name accepted by Google Cloud Text-to-Speech.'
                        : 'Enter the full file path of a voice model supported by Piper.'
                  }
                >
                  <input
                    name="tts_model"
                    defaultValue={query.data?.tts.model}
                    placeholder={
                      compatibleTts
                        ? 'tts-1 or provider model ID'
                        : googleTts
                          ? 'Voice name'
                          : 'Full path to Piper voice model'
                    }
                    required
                  />
                </Field>
                {compatibleTts && (
                  <Field
                    full
                    label="Voice"
                    hint="The voice identifier accepted by the selected model."
                  >
                    <input
                      name="tts_voice"
                      defaultValue={query.data?.tts.voice}
                      placeholder="alloy or provider voice ID"
                      required
                    />
                  </Field>
                )}
                {googleTts && (
                  <Field full label="Language code">
                    <input name="tts_language" defaultValue={query.data?.tts.language || 'en-US'} />
                  </Field>
                )}
                {(compatibleTts || googleTts) && (
                  <>
                    <Field
                      full
                      label="API URL"
                      hint={
                        compatibleTts
                          ? 'An API root or the complete /audio/speech endpoint.'
                          : 'The Google Cloud Text-to-Speech API root.'
                      }
                    >
                      <input
                        type="url"
                        name="tts_base_url"
                        defaultValue={query.data?.tts.base_url}
                        placeholder={
                          compatibleTts
                            ? 'https://provider.example/v1'
                            : 'https://texttospeech.googleapis.com/v1'
                        }
                        required
                      />
                    </Field>
                    <Field
                      full
                      label="API key"
                      hint={
                        query.data?.tts.api_key_configured
                          ? 'A key is saved. Leave blank to keep it.'
                          : compatibleTts
                            ? 'Optional for local or unauthenticated endpoints.'
                            : 'Required by the Google transport.'
                      }
                    >
                      <input type="password" name="tts_api_key" />
                    </Field>
                  </>
                )}
              </div>
            </Card>
          </div>
          <Feedback
            message={update.error instanceof Error ? update.error.message : success}
            kind={success ? 'success' : 'error'}
          />
          <div className="form-footer">
            <Button variant="primary" icon={<Save size={14} />} busy={update.isPending}>
              Save voice settings
            </Button>
          </div>
        </form>
      </div>
    </>
  )
}
