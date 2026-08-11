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
    ttsProvider = tts || query.data?.tts.provider || 'piper'
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
        language: values.tts_language,
        base_url: values.tts_base_url,
        api_key: values.tts_api_key,
      },
    })
  }
  return (
    <>
      <PageHeader title="Voice" description="Configure speech recognition and voice output" />
      <div className="page-content">
        <form className="stack" onSubmit={submit}>
          <div className="settings-grid">
            <Card title="Speech-to-text" description="Choose how voice messages are transcribed.">
              <div className="card__body form-grid">
                <Field full label="Provider">
                  <select
                    name="stt_provider"
                    value={sttProvider}
                    onChange={(e) => setStt(e.target.value)}
                  >
                    <option value="local_whisper">Faster Whisper (local)</option>
                    <option value="groq">Groq Whisper (cloud)</option>
                  </select>
                </Field>
                <Field full label={sttProvider === 'groq' ? 'Model ID' : 'Model name or path'}>
                  <input name="stt_model" defaultValue={query.data?.stt.model} required />
                </Field>
                <Field full label="Language">
                  <input name="stt_language" defaultValue={query.data?.stt.language || 'auto'} />
                </Field>
                {sttProvider === 'groq' && (
                  <>
                    <Field full label="Base URL">
                      <input name="stt_base_url" defaultValue={query.data?.stt.base_url} />
                    </Field>
                    <Field
                      full
                      label="API key"
                      hint={
                        query.data?.stt.api_key_configured
                          ? 'A key is saved. Leave blank to keep it.'
                          : 'No key saved.'
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
                <Field full label="Provider">
                  <select
                    name="tts_provider"
                    value={ttsProvider}
                    onChange={(e) => setTts(e.target.value)}
                  >
                    <option value="piper">Piper (local)</option>
                    <option value="google">Google Cloud Text-to-Speech</option>
                  </select>
                </Field>
                <Field full label={ttsProvider === 'google' ? 'Voice name' : 'Voice model path'}>
                  <input name="tts_model" defaultValue={query.data?.tts.model} required />
                </Field>
                {ttsProvider === 'google' && (
                  <>
                    <Field full label="Language code">
                      <input
                        name="tts_language"
                        defaultValue={query.data?.tts.language || 'en-US'}
                      />
                    </Field>
                    <Field full label="Base URL">
                      <input name="tts_base_url" defaultValue={query.data?.tts.base_url} />
                    </Field>
                    <Field
                      full
                      label="API key"
                      hint={
                        query.data?.tts.api_key_configured
                          ? 'A key is saved. Leave blank to keep it.'
                          : 'No key saved.'
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
