import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AudioLines, Mic2, Save } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { keys } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'

export function VoicePage() {
  const client = useQueryClient()
  const settings = useQuery({ queryKey: keys.voice, queryFn: api.voice.get })
  const models = useQuery({ queryKey: keys.voiceModels, queryFn: api.voiceModels.list })
  const update = useMutation({ mutationFn: api.voice.update })
  const [sttModelId, setSttModelId] = useState(0)
  const [ttsModelId, setTtsModelId] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!settings.data || dirty) return
    setSttModelId(Number(settings.data.stt.model_id || 0))
    setTtsModelId(Number(settings.data.tts.model_id || 0))
  }, [dirty, settings.data])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setMessage('')
    try {
      await update.mutateAsync({ stt_model_id: sttModelId, tts_model_id: ttsModelId })
      await client.invalidateQueries({ queryKey: keys.voice })
      setDirty(false)
      setMessage('Voice models saved.')
    } catch {
      // The request error is shown below with its server-provided message.
    }
  }

  if (settings.isLoading || models.isLoading) {
    return (
      <>
        <PageHeader
          title="Voice"
          description="Choose the saved models Mounir uses for listening and speaking."
        />
        <Loading />
      </>
    )
  }

  const loadError = settings.error || models.error
  if (loadError || !settings.data || !models.data) {
    return (
      <>
        <PageHeader
          title="Voice"
          description="Choose the saved models Mounir uses for listening and speaking."
        />
        <div className="page-content">
          <Feedback
            message={
              loadError instanceof Error ? loadError.message : 'Voice settings could not be loaded.'
            }
          />
        </div>
      </>
    )
  }

  const transcriptionModels = models.data.filter((model) => model.kind === 'stt')
  const speechModels = models.data.filter((model) => model.kind === 'tts')
  const canSave = Boolean(sttModelId && ttsModelId && dirty)

  return (
    <>
      <PageHeader
        title="Voice"
        description="Choose the saved models Mounir uses for listening and speaking."
        actions={
          canSave ? (
            <Button
              variant="primary"
              icon={<Save size={14} />}
              type="submit"
              form="voice-model-selection"
              busy={update.isPending}
            >
              Save changes
            </Button>
          ) : undefined
        }
      />
      <div className="page-content stack">
        <Card
          title="Active voice models"
          description="Configurations are created once in Models and can be reused here without duplicating credentials."
        >
          <form id="voice-model-selection" className="card__body form-grid" onSubmit={submit}>
            <Field
              full
              label="Transcription model"
              hint="Used to convert incoming speech into text."
            >
              <div className="voice-model-select">
                <Mic2 size={15} aria-hidden="true" />
                <select
                  value={sttModelId}
                  onChange={(event) => {
                    setSttModelId(Number(event.target.value))
                    setDirty(true)
                    setMessage('')
                    update.reset()
                  }}
                  required
                >
                  <option value={0} disabled>
                    Select a transcription model
                  </option>
                  {transcriptionModels.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.name} — {model.provider_name || model.provider} — {model.model}
                    </option>
                  ))}
                </select>
              </div>
            </Field>

            <Field full label="Speech model" hint="Used to generate Mounir's spoken responses.">
              <div className="voice-model-select">
                <AudioLines size={15} aria-hidden="true" />
                <select
                  value={ttsModelId}
                  onChange={(event) => {
                    setTtsModelId(Number(event.target.value))
                    setDirty(true)
                    setMessage('')
                    update.reset()
                  }}
                  required
                >
                  <option value={0} disabled>
                    Select a speech model
                  </option>
                  {speechModels.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.name} — {model.provider_name || model.provider} — {model.model}
                    </option>
                  ))}
                </select>
              </div>
            </Field>

            {(!transcriptionModels.length || !speechModels.length) && (
              <div className="guidance">
                {!transcriptionModels.length && 'Add a transcription model. '}
                {!speechModels.length && 'Add a speech model. '}
                <Link to="/admin/models">Open Models</Link>
              </div>
            )}
            <div className="field--full">
              <Feedback
                message={update.error instanceof Error ? update.error.message : message}
                kind={message ? 'success' : 'error'}
              />
            </div>
          </form>
        </Card>
      </div>
    </>
  )
}
