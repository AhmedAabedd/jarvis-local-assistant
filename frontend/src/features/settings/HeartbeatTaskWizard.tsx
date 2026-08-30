import { Search } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import type { HeartbeatCapability, HeartbeatTask } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { Switch } from '../../components/ui/Switch'
import { readable } from '../resources/helpers'

type ScheduleUnit = 'minutes' | 'hours' | 'days' | 'months'

const SCHEDULE_UNIT_MINUTES: Record<ScheduleUnit, number> = {
  minutes: 1,
  hours: 60,
  days: 1440,
  months: 43200,
}

const SCHEDULE_UNIT_MAX: Record<ScheduleUnit, number> = {
  minutes: 525600,
  hours: 8760,
  days: 365,
  months: 12,
}

interface TaskDraft {
  name: string
  enabled: boolean
  scheduleValue: number
  scheduleUnit: ScheduleUnit
  executionLimit: number
  remainingRuns: number
  finiteExecutionLimit: number
  finiteRemainingRuns: number
  prompt: string
  notifyTelegram: boolean
  notifyWhatsapp: boolean
  selectedAgents: Set<string>
  selectedTools: Set<string>
}

function toolKey(agent: string, tool: string) {
  return `${agent}::${tool}`
}

function capabilityKey(agent: HeartbeatCapability) {
  return agent.key || agent.id || ''
}

function safeTools(agent?: HeartbeatCapability) {
  return agent?.tools.filter((tool) => !tool.requires_confirmation) || []
}

function scheduleParts(minutes: number): { value: number; unit: ScheduleUnit } {
  if (minutes >= SCHEDULE_UNIT_MINUTES.months && minutes % SCHEDULE_UNIT_MINUTES.months === 0) {
    return { value: minutes / SCHEDULE_UNIT_MINUTES.months, unit: 'months' }
  }
  if (minutes >= SCHEDULE_UNIT_MINUTES.days && minutes % SCHEDULE_UNIT_MINUTES.days === 0) {
    return { value: minutes / SCHEDULE_UNIT_MINUTES.days, unit: 'days' }
  }
  if (minutes >= SCHEDULE_UNIT_MINUTES.hours && minutes % SCHEDULE_UNIT_MINUTES.hours === 0) {
    return { value: minutes / SCHEDULE_UNIT_MINUTES.hours, unit: 'hours' }
  }
  return { value: minutes, unit: 'minutes' }
}

function scheduleMinutes(value: number, unit: ScheduleUnit) {
  return value * SCHEDULE_UNIT_MINUTES[unit]
}

export function formatHeartbeatInterval(minutes: number) {
  const schedule = scheduleParts(minutes)
  const singular = schedule.value === 1
  const unit = singular ? schedule.unit.slice(0, -1) : schedule.unit
  return singular ? unit : `${schedule.value} ${unit}`
}

function taskDraft(task?: HeartbeatTask): TaskDraft {
  const schedule = scheduleParts(task?.interval_minutes || 30)
  const executionLimit = task?.execution_limit ?? -1
  const remainingRuns = task?.remaining_runs ?? -1
  return {
    name: task?.name || '',
    enabled: task?.enabled || false,
    scheduleValue: schedule.value,
    scheduleUnit: schedule.unit,
    executionLimit,
    remainingRuns,
    finiteExecutionLimit: executionLimit === -1 ? 1 : executionLimit,
    finiteRemainingRuns: remainingRuns === -1 ? 1 : remainingRuns,
    prompt: task?.instructions || '',
    notifyTelegram: task?.notify_telegram ?? true,
    notifyWhatsapp: task?.notify_whatsapp ?? false,
    selectedAgents: new Set(task?.selected_agents || []),
    selectedTools: new Set(
      (task?.selected_tools || []).map((item) => toolKey(item.agent_key, item.tool_name)),
    ),
  }
}

function taskPayload(draft: TaskDraft) {
  return {
    name: draft.name.trim(),
    enabled: draft.enabled,
    interval_minutes: scheduleMinutes(draft.scheduleValue, draft.scheduleUnit),
    execution_limit: draft.executionLimit,
    instructions: draft.prompt.trim(),
    notify_telegram: draft.notifyTelegram,
    notify_whatsapp: draft.notifyWhatsapp,
    selected_agents: [...draft.selectedAgents],
    selected_tools: [...draft.selectedTools].map((value) => {
      const separator = value.indexOf('::')
      return {
        agent_key: value.slice(0, separator),
        tool_name: value.slice(separator + 2),
      }
    }),
  }
}

function HeartbeatToolPicker({
  capabilities,
  selectedAgents,
  selectedTools,
  onChange,
}: {
  capabilities: HeartbeatCapability[]
  selectedAgents: Set<string>
  selectedTools: Set<string>
  onChange: (selectedAgents: Set<string>, selectedTools: Set<string>) => void
}) {
  const [agentQuery, setAgentQuery] = useState('')
  const [toolQuery, setToolQuery] = useState('')
  const [activeAgentKey, setActiveAgentKey] = useState(
    () => [...selectedAgents][0] || capabilityKey(capabilities[0]),
  )
  const normalizedAgentQuery = agentQuery.trim().toLocaleLowerCase()
  const visibleAgents = useMemo(
    () =>
      capabilities.filter(
        (agent) =>
          !normalizedAgentQuery ||
          agent.name.toLocaleLowerCase().includes(normalizedAgentQuery) ||
          (agent.description || '').toLocaleLowerCase().includes(normalizedAgentQuery),
      ),
    [capabilities, normalizedAgentQuery],
  )
  const activeAgent =
    capabilities.find((agent) => capabilityKey(agent) === activeAgentKey) || capabilities[0]
  const activeKey = activeAgent ? capabilityKey(activeAgent) : ''
  const availableTools = safeTools(activeAgent)
  const normalizedToolQuery = toolQuery.trim().toLocaleLowerCase()
  const visibleTools = availableTools.filter(
    (tool) =>
      !normalizedToolQuery ||
      readable(tool.name).toLocaleLowerCase().includes(normalizedToolQuery) ||
      tool.name.toLocaleLowerCase().includes(normalizedToolQuery) ||
      (tool.description || '').toLocaleLowerCase().includes(normalizedToolQuery),
  )
  const enabledCount = availableTools.filter((tool) =>
    selectedTools.has(toolKey(activeKey, tool.name)),
  ).length
  const allEnabled = Boolean(availableTools.length && enabledCount === availableTools.length)
  const protectedCount = (activeAgent?.tools.length || 0) - availableTools.length

  const replaceAgentTools = (names: string[]) => {
    const nextAgents = new Set(selectedAgents)
    const nextTools = new Set(selectedTools)
    for (const selected of nextTools) {
      if (selected.startsWith(`${activeKey}::`)) nextTools.delete(selected)
    }
    if (names.length) {
      nextAgents.add(activeKey)
      names.forEach((name) => nextTools.add(toolKey(activeKey, name)))
    } else {
      nextAgents.delete(activeKey)
    }
    onChange(nextAgents, nextTools)
  }

  const toggleTool = (name: string, checked: boolean) => {
    const names = new Set(
      availableTools
        .filter((tool) => selectedTools.has(toolKey(activeKey, tool.name)))
        .map((tool) => tool.name),
    )
    checked ? names.add(name) : names.delete(name)
    replaceAgentTools([...names])
  }

  if (!capabilities.length) {
    return <div className="empty-state mcp-access__empty">No task tools are available.</div>
  }

  return (
    <div className="mcp-access heartbeat-tool-picker">
      <aside className="mcp-access__sidebar">
        <strong className="mcp-access__sidebar-title">Agents</strong>
        <label className="resource-search mcp-access__search">
          <Search size={13} />
          <input
            type="search"
            value={agentQuery}
            onChange={(event) => setAgentQuery(event.target.value)}
            placeholder="Search agents…"
            aria-label="Search task agents"
          />
        </label>
        <div className="mcp-access__server-list">
          {visibleAgents.length ? (
            visibleAgents.map((agent) => {
              const key = capabilityKey(agent)
              return (
                <button
                  className={`mcp-access__server-item ${activeKey === key ? 'is-active' : ''}`}
                  type="button"
                  key={key}
                  aria-pressed={activeKey === key}
                  onClick={() => {
                    setActiveAgentKey(key)
                    setToolQuery('')
                  }}
                >
                  {agent.name}
                </button>
              )
            })
          ) : (
            <div className="mcp-access__message">No agents match this search.</div>
          )}
        </div>
      </aside>

      <section className="mcp-access__detail">
        {activeAgent ? (
          <>
            <header className="mcp-access__detail-header">
              <strong>{activeAgent.name}</strong>
              <div className="mcp-access__counts">
                <strong>{enabledCount} tools enabled</strong>
                <span>{availableTools.length} available</span>
              </div>
              <span className="heartbeat-tool-picker__header-spacer" aria-hidden="true" />
            </header>
            <div className="mcp-access__tool-toolbar">
              <label className="resource-search mcp-access__search">
                <Search size={13} />
                <input
                  type="search"
                  value={toolQuery}
                  onChange={(event) => setToolQuery(event.target.value)}
                  placeholder="Search tools…"
                  aria-label={`Search tools from ${activeAgent.name}`}
                />
              </label>
              <button
                className={`mcp-access__select-all ${allEnabled ? 'is-active' : ''}`}
                type="button"
                disabled={!availableTools.length}
                aria-pressed={allEnabled}
                onClick={() =>
                  replaceAgentTools(allEnabled ? [] : availableTools.map((tool) => tool.name))
                }
              >
                Select all
              </button>
            </div>
            <div className="mcp-access__tool-list">
              {visibleTools.length ? (
                visibleTools.map((tool) => (
                  <label key={tool.name}>
                    <input
                      type="checkbox"
                      checked={selectedTools.has(toolKey(activeKey, tool.name))}
                      onChange={(event) => toggleTool(tool.name, event.target.checked)}
                    />
                    <span>
                      <strong>{tool.label || readable(tool.name)}</strong>
                      <small>{tool.description || 'No description available.'}</small>
                    </span>
                  </label>
                ))
              ) : (
                <div className="mcp-access__message">
                  {normalizedToolQuery ? 'No tools match this search.' : 'No safe tools available.'}
                </div>
              )}
              {protectedCount > 0 && (
                <div className="mcp-access__message">
                  {protectedCount} approval-required tool{protectedCount === 1 ? '' : 's'} excluded.
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="empty-state mcp-access__detail-empty">Select an agent.</div>
        )}
      </section>
    </div>
  )
}

export function HeartbeatTaskWizard({
  open,
  task,
  capabilities,
  busy,
  requestError,
  onClose,
  onSubmit,
}: {
  open: boolean
  task?: HeartbeatTask
  capabilities: HeartbeatCapability[]
  busy?: boolean
  requestError?: string
  onClose: () => void
  onSubmit: (payload: ReturnType<typeof taskPayload>) => Promise<void>
}) {
  const formId = task ? `heartbeat-edit-${task.id}` : 'heartbeat-create'
  const [draft, setDraft] = useState(() => taskDraft(task))
  const [error, setError] = useState('')
  const updateDraft = (changes: Partial<TaskDraft>) =>
    setDraft((current) => ({ ...current, ...changes }))

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const minutes = scheduleMinutes(draft.scheduleValue, draft.scheduleUnit)
    if (!draft.name.trim()) return setError('Enter a task name.')
    if (!draft.prompt.trim()) return setError('Enter a prompt for Mounir.')
    if (!Number.isInteger(minutes) || minutes < 5 || minutes > 525600) {
      return setError('Choose a whole-number schedule between 5 minutes and 1 year.')
    }
    if (
      draft.executionLimit !== -1 &&
      (!Number.isInteger(draft.executionLimit) ||
        draft.executionLimit < 1 ||
        draft.executionLimit > 10000)
    ) {
      return setError('Runs must be a whole number between 1 and 10,000.')
    }
    if (draft.enabled && !draft.selectedAgents.size) {
      return setError('Select at least one tool before enabling the task.')
    }
    try {
      await onSubmit(taskPayload(draft))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save the heartbeat task.')
    }
  }

  const setRunForever = (forever: boolean) => {
    if (forever) {
      const finiteLimit =
        draft.executionLimit === -1 ? draft.finiteExecutionLimit : draft.executionLimit
      const finiteRemaining = draft.remainingRuns === -1 ? finiteLimit : draft.remainingRuns
      updateDraft({
        executionLimit: -1,
        remainingRuns: -1,
        finiteExecutionLimit: finiteLimit,
        finiteRemainingRuns: finiteRemaining,
      })
      return
    }
    updateDraft({
      executionLimit: draft.finiteExecutionLimit,
      remainingRuns: draft.finiteRemainingRuns,
    })
  }

  return (
    <Modal
      open={open}
      wide
      integrated
      className="modal--compact-write-form modal--heartbeat-write-form"
      title={task ? `Edit ${task.name}` : 'Create heartbeat'}
      description="Configure the task, schedule, delivery, and tools in one form."
      onClose={onClose}
    >
      <div className="compact-write-modal-form">
        <form id={formId} className="heartbeat-write-form" onSubmit={submit}>
          <div className="form-grid">
            <Field full label="Task name">
              <input
                autoFocus
                maxLength={120}
                value={draft.name}
                onChange={(event) => updateDraft({ name: event.target.value })}
                placeholder="Monitor important email"
              />
            </Field>
            <Field
              full
              label="Prompt"
              hint="Describe the exact result Mounir should check for or produce."
            >
              <textarea
                rows={6}
                maxLength={4000}
                value={draft.prompt}
                onChange={(event) => updateDraft({ prompt: event.target.value })}
                placeholder="What should Mounir do?"
              />
            </Field>
          </div>

          <div className="heartbeat-write-form__section">
            <div className="heartbeat-write-form__section-heading">
              <strong>Schedule</strong>
              <small>Choose how often the task runs and when it should stop.</small>
            </div>
            <div className="heartbeat-wizard__schedule-row">
              <Field label="Every">
                <input
                  type="number"
                  min={draft.scheduleUnit === 'minutes' ? 5 : 1}
                  max={SCHEDULE_UNIT_MAX[draft.scheduleUnit]}
                  step={1}
                  value={draft.scheduleValue}
                  onChange={(event) => updateDraft({ scheduleValue: Number(event.target.value) })}
                />
              </Field>
              <Field label="Unit">
                <select
                  value={draft.scheduleUnit}
                  onChange={(event) => {
                    const unit = event.target.value as ScheduleUnit
                    const minimum = unit === 'minutes' ? 5 : 1
                    updateDraft({
                      scheduleUnit: unit,
                      scheduleValue: Math.max(
                        minimum,
                        Math.min(draft.scheduleValue, SCHEDULE_UNIT_MAX[unit]),
                      ),
                    })
                  }}
                >
                  <option value="minutes">Minutes</option>
                  <option value="hours">Hours</option>
                  <option value="days">Days</option>
                  <option value="months">Months</option>
                </select>
              </Field>
              <div className="heartbeat-run-forever-field">
                <span className="field__label">Run forever</span>
                <div>
                  <Switch
                    checked={draft.executionLimit === -1}
                    onChange={setRunForever}
                    label="Run forever"
                  />
                </div>
              </div>
              <Field label="Runs">
                <input
                  type="number"
                  min={1}
                  max={10000}
                  step={1}
                  disabled={draft.executionLimit === -1}
                  value={draft.executionLimit === -1 ? '' : draft.executionLimit}
                  placeholder="Unlimited"
                  onChange={(event) => {
                    const executionLimit = Number(event.target.value)
                    updateDraft({
                      executionLimit,
                      remainingRuns: executionLimit,
                      finiteExecutionLimit: executionLimit,
                      finiteRemainingRuns: executionLimit,
                    })
                  }}
                />
              </Field>
            </div>
            <span className="field__hint heartbeat-wizard__schedule-hint">
              {draft.executionLimit === -1
                ? 'Runs continuously until paused.'
                : `${draft.remainingRuns} execution${draft.remainingRuns === 1 ? '' : 's'} remaining.`}
              {draft.scheduleUnit === 'months' ? ' A month is a 30-day interval.' : ''}
            </span>
          </div>

          <div className="heartbeat-write-form__section">
            <div className="heartbeat-write-form__section-heading">
              <strong>Delivery</strong>
              <small>Alerts always appear in the app. Choose additional paired channels.</small>
            </div>
            <div className="heartbeat-channel-list heartbeat-wizard__channels">
              <div>
                <span>
                  <strong>Telegram</strong>
                  <small>Paired chat</small>
                </span>
                <Switch
                  checked={draft.notifyTelegram}
                  onChange={(notifyTelegram) => updateDraft({ notifyTelegram })}
                  label="Telegram notifications"
                />
              </div>
              <div>
                <span>
                  <strong>WhatsApp</strong>
                  <small>Paired phone</small>
                </span>
                <Switch
                  checked={draft.notifyWhatsapp}
                  onChange={(notifyWhatsapp) => updateDraft({ notifyWhatsapp })}
                  label="WhatsApp notifications"
                />
              </div>
            </div>
          </div>

          <div className="heartbeat-write-form__section heartbeat-write-form__section--tools">
            <div className="heartbeat-write-form__section-heading">
              <strong>Tools</strong>
              <small>Select the safe tools Mounir can use for this task.</small>
            </div>
            <HeartbeatToolPicker
              capabilities={capabilities}
              selectedAgents={draft.selectedAgents}
              selectedTools={draft.selectedTools}
              onChange={(selectedAgents, selectedTools) =>
                updateDraft({ selectedAgents, selectedTools })
              }
            />
          </div>

          <Feedback message={error || requestError || ''} />
        </form>
      </div>
      <div className="compact-form-actions">
        <Button variant="primary" type="submit" form={formId} busy={busy}>
          {task ? 'Save changes' : 'Create task'}
        </Button>
      </div>
    </Modal>
  )
}
