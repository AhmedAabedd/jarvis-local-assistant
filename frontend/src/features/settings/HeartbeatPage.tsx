import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, Save } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { HeartbeatCapability } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { Status } from '../../components/ui/Status'
import { Switch } from '../../components/ui/Switch'
import { keys } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'
import { readable } from '../resources/helpers'

function toolKey(agent: string, tool: string) {
  return `${agent}::${tool}`
}
function agentKey(agent: HeartbeatCapability) {
  return agent.key || agent.id || ''
}

export function HeartbeatPage() {
  const client = useQueryClient(),
    query = useQuery({ queryKey: keys.heartbeat, queryFn: api.heartbeat.get }),
    state = query.data
  const [enabled, setEnabled] = useState<boolean>(),
    [interval, setIntervalValue] = useState<number>(),
    [instructions, setInstructions] = useState<string>(),
    [telegram, setTelegram] = useState<boolean>(),
    [whatsapp, setWhatsapp] = useState<boolean>(),
    [selection, setSelection] = useState<Set<string>>(),
    [success, setSuccess] = useState('')
  useEffect(() => {
    if (!state || selection) return
    const selected = new Set<string>()
    state.capabilities.forEach((agent) =>
      agent.tools.forEach((tool) => {
        if (tool.selected) selected.add(toolKey(agentKey(agent), tool.name))
      }),
    )
    setSelection(selected)
  }, [state, selection])
  const payload = () => ({
    enabled: enabled ?? state?.enabled,
    interval_minutes: interval ?? state?.interval_minutes,
    instructions: instructions ?? state?.instructions,
    notify_telegram: telegram ?? state?.notify_telegram,
    notify_whatsapp: whatsapp ?? state?.notify_whatsapp,
    selected_tools: [...(selection || [])].map((value) => {
      const [agent_key, tool_name] = value.split('::')
      return { agent_key, tool_name }
    }),
  })
  const update = useMutation({
    mutationFn: () => api.heartbeat.update(payload()),
    onSuccess: async (data) => {
      client.setQueryData(keys.heartbeat, data)
      setSuccess('Heartbeat settings saved.')
    },
  })
  const run = useMutation({
    mutationFn: async () => {
      await api.heartbeat.update(payload())
      return api.heartbeat.run()
    },
    onSuccess: (data) => {
      client.setQueryData(keys.heartbeat, data)
      setSuccess('Heartbeat run completed.')
    },
  })
  const capabilities = state?.capabilities || [],
    selected = selection || new Set<string>()
  const selectedCount = useMemo(() => selected.size, [selected])
  if (query.isLoading || !state)
    return (
      <>
        <PageHeader
          title="Heartbeat"
          description="Schedule automatic checks using approved tools"
        />
        <Loading />
      </>
    )
  const setTool = (key: string, checked: boolean) => {
    const next = new Set(selected)
    checked ? next.add(key) : next.delete(key)
    setSelection(next)
  }
  return (
    <>
      <PageHeader
        title="Heartbeat"
        description="Schedule automatic checks using approved tools"
        actions={
          <>
            <Button icon={<Play size={14} />} busy={run.isPending} onClick={() => run.mutate()}>
              Run now
            </Button>
            <Button
              variant="primary"
              icon={<Save size={14} />}
              busy={update.isPending}
              onClick={() => update.mutate()}
            >
              Save settings
            </Button>
          </>
        }
      />
      <div className="page-content stack">
        <Card>
          <div className="setting-row">
            <span>
              <strong>Enable Heartbeat</strong>
              <small>Run approved checks in the background on this device.</small>
            </span>
            <Switch
              checked={enabled ?? state.enabled}
              onChange={setEnabled}
              label="Enable heartbeat"
            />
          </div>
        </Card>
        <div className="settings-grid">
          <Card
            title="Schedule and instructions"
            description="Tell the assistant what changes deserve a notification."
          >
            <div className="card__body form-grid">
              <Field full label="Check interval">
                <select
                  value={interval ?? state.interval_minutes}
                  onChange={(e) => setIntervalValue(Number(e.target.value))}
                >
                  <option value={5}>Every 5 minutes</option>
                  <option value={15}>Every 15 minutes</option>
                  <option value={30}>Every 30 minutes</option>
                  <option value={60}>Every hour</option>
                  <option value={180}>Every 3 hours</option>
                  <option value={360}>Every 6 hours</option>
                  <option value={720}>Every 12 hours</option>
                  <option value={1440}>Daily</option>
                </select>
              </Field>
              <Field
                full
                label="What should Heartbeat watch?"
                hint="Quiet runs do not create a notification."
              >
                <textarea
                  rows={7}
                  maxLength={2000}
                  value={instructions ?? state.instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  placeholder="Notify me when…"
                />
              </Field>
            </div>
          </Card>
          <Card
            title="Notification delivery"
            description="Dashboard alerts are always saved locally."
          >
            <div className="card__body stack">
              <div className="availability">
                <span>
                  <strong>Telegram</strong>
                  <small>Also deliver alerts to the paired Telegram chat.</small>
                </span>
                <Switch
                  checked={telegram ?? state.notify_telegram}
                  onChange={setTelegram}
                  label="Telegram notifications"
                />
              </div>
              <div className="availability">
                <span>
                  <strong>WhatsApp</strong>
                  <small>Also deliver alerts to the paired WhatsApp phone.</small>
                </span>
                <Switch
                  checked={whatsapp ?? state.notify_whatsapp}
                  onChange={setWhatsapp}
                  label="WhatsApp notifications"
                />
              </div>
            </div>
          </Card>
        </div>
        <Card
          title="Approved capabilities"
          description={`${selectedCount} safe tool${selectedCount === 1 ? '' : 's'} selected. Interactive actions cannot run unattended.`}
        >
          <div className="card__body stack">
            {capabilities.map((agent) => {
              const safe = agent.tools.filter((tool) => !tool.requires_confirmation)
              const all =
                safe.length > 0 &&
                safe.every((tool) => selected.has(toolKey(agentKey(agent), tool.name)))
              return (
                <section className="key-value-editor" key={agentKey(agent)}>
                  <div className="key-value-editor__title">
                    <span>
                      <strong>{agent.name}</strong>
                      <small className="field__hint" style={{ display: 'block', marginTop: 4 }}>
                        {agent.description}
                      </small>
                    </span>
                    <label className="tool-option" style={{ display: 'flex' }}>
                      <input
                        type="checkbox"
                        checked={all}
                        onChange={(e) => {
                          const next = new Set(selected)
                          safe.forEach((tool) =>
                            e.target.checked
                              ? next.add(toolKey(agentKey(agent), tool.name))
                              : next.delete(toolKey(agentKey(agent), tool.name)),
                          )
                          setSelection(next)
                        }}
                      />{' '}
                      Select all
                    </label>
                  </div>
                  <div className="tool-list">
                    {agent.tools.map((tool) => (
                      <label className="tool-option" key={tool.name}>
                        <input
                          type="checkbox"
                          disabled={tool.requires_confirmation}
                          checked={selected.has(toolKey(agentKey(agent), tool.name))}
                          onChange={(e) =>
                            setTool(toolKey(agentKey(agent), tool.name), e.target.checked)
                          }
                        />
                        <span>
                          <strong>{readable(tool.name)}</strong>
                          <small>
                            {tool.requires_confirmation
                              ? 'Requires interaction and cannot be used by Heartbeat.'
                              : tool.description || 'Approval-free action.'}
                          </small>
                        </span>
                      </label>
                    ))}
                  </div>
                </section>
              )
            })}
          </div>
        </Card>
        <Card title="Recent runs" description="The latest scheduled and manual checks.">
          <div className="run-list">
            {!state.recent_runs.length && <div className="empty-state">No runs yet.</div>}
            {state.recent_runs.map((run) => (
              <div className="run-row" key={run.id}>
                <Status value={run.status} />
                <p>{run.summary || run.error || `${run.trigger || 'Scheduled'} check`}</p>
                <time>{run.started_at ? new Date(run.started_at).toLocaleString() : ''}</time>
              </div>
            ))}
          </div>
        </Card>
        <Feedback
          message={
            (update.error || run.error) instanceof Error
              ? (update.error || (run.error as Error)).message
              : success
          }
          kind={success ? 'success' : 'error'}
        />
      </div>
    </>
  )
}
