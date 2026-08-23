import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Bell, Clock, History, Plus, Play, Save, Trash2, Users } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import type { HeartbeatCapability, HeartbeatSettings, HeartbeatTask } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { SectionTabs } from '../../components/ui/SectionTabs'
import { Status } from '../../components/ui/Status'
import { Switch } from '../../components/ui/Switch'
import { keys } from '../../hooks/useStudioData'
import { readable } from '../resources/helpers'
import { PageHeader } from '../studio/PageHeader'

interface TaskDraft {
  id?: number
  name: string
  enabled: boolean
  interval_minutes: number
  execution_limit: number
  remaining_runs: number
  saved_execution_limit: number
  saved_remaining_runs: number
  instructions: string
  notify_telegram: boolean
  notify_whatsapp: boolean
  selectedAgents: Set<string>
  selectedTools: Set<string>
  recent_runs: HeartbeatTask['recent_runs']
  last_status: string
  next_run_at?: string | null
}

type EditorTab = 'setup' | 'access' | 'history'

function toolKey(agent: string, tool: string) {
  return `${agent}::${tool}`
}

function capabilityKey(agent: HeartbeatCapability) {
  return agent.key || agent.id || ''
}

function taskDraft(task: HeartbeatTask): TaskDraft {
  return {
    id: task.id,
    name: task.name,
    enabled: task.enabled,
    interval_minutes: task.interval_minutes,
    execution_limit: task.execution_limit,
    remaining_runs: task.remaining_runs,
    saved_execution_limit: task.execution_limit,
    saved_remaining_runs: task.remaining_runs,
    instructions: task.instructions,
    notify_telegram: task.notify_telegram,
    notify_whatsapp: task.notify_whatsapp,
    selectedAgents: new Set(task.selected_agents),
    selectedTools: new Set(
      task.selected_tools.map((item) => toolKey(item.agent_key, item.tool_name)),
    ),
    recent_runs: task.recent_runs || [],
    last_status: task.last_status,
    next_run_at: task.next_run_at,
  }
}

function newTask(): TaskDraft {
  return {
    name: '',
    enabled: false,
    interval_minutes: 30,
    execution_limit: -1,
    remaining_runs: -1,
    saved_execution_limit: -1,
    saved_remaining_runs: -1,
    instructions: '',
    notify_telegram: true,
    notify_whatsapp: false,
    selectedAgents: new Set(),
    selectedTools: new Set(),
    recent_runs: [],
    last_status: 'never',
  }
}

function taskPayload(draft: TaskDraft) {
  return {
    name: draft.name,
    enabled: draft.enabled,
    interval_minutes: draft.interval_minutes,
    execution_limit: draft.execution_limit,
    instructions: draft.instructions,
    notify_telegram: draft.notify_telegram,
    notify_whatsapp: draft.notify_whatsapp,
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

function intervalLabel(minutes: number) {
  if (minutes < 60) return `${minutes} min`
  if (minutes === 60) return 'hour'
  if (minutes === 1440) return 'day'
  return `${minutes / 60} hours`
}

export function HeartbeatPage() {
  const client = useQueryClient()
  const query = useQuery({ queryKey: keys.heartbeat, queryFn: api.heartbeat.get })
  const state = query.data
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedTask = searchParams.get('task')
  const [draft, setDraft] = useState<TaskDraft>()
  const [activeTab, setActiveTab] = useState<EditorTab>('setup')
  const [success, setSuccess] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<HeartbeatTask | null>(null)
  const runLimitInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!selectedTask) {
      setDraft(undefined)
      return
    }
    if (selectedTask === 'new') {
      setDraft((current) => (current && !current.id ? current : newTask()))
      return
    }
    const task = state?.tasks.find((item) => String(item.id) === selectedTask)
    setDraft(task ? taskDraft(task) : undefined)
  }, [selectedTask, state?.tasks])

  const syncTask = (task: HeartbeatTask) => {
    client.setQueryData<HeartbeatSettings>(keys.heartbeat, (current) => {
      if (!current) return current
      const exists = current.tasks.some((item) => item.id === task.id)
      return {
        ...current,
        tasks: exists
          ? current.tasks.map((item) => (item.id === task.id ? task : item))
          : [...current.tasks, task],
      }
    })
    setDraft(taskDraft(task))
    setSearchParams({ task: String(task.id) })
  }

  const save = useMutation({
    mutationFn: async (value: TaskDraft) =>
      value.id
        ? api.heartbeat.updateTask(value.id, taskPayload(value))
        : api.heartbeat.createTask(taskPayload(value)),
    onSuccess: (task) => {
      syncTask(task)
      setSuccess('Heartbeat task saved.')
    },
  })
  const run = useMutation({
    mutationFn: async (value: TaskDraft) => {
      const saved = value.id
        ? await api.heartbeat.updateTask(value.id, taskPayload(value))
        : await api.heartbeat.createTask(taskPayload(value))
      return api.heartbeat.runTask(saved.id)
    },
    onSuccess: (task) => {
      syncTask(task)
      setSuccess('Heartbeat task completed.')
    },
  })
  const remove = useMutation({
    mutationFn: (id: number) => api.heartbeat.removeTask(id),
    onSuccess: (_result, id) => {
      client.setQueryData<HeartbeatSettings>(keys.heartbeat, (current) =>
        current ? { ...current, tasks: current.tasks.filter((task) => task.id !== id) } : current,
      )
      setDraft(undefined)
      setSearchParams({})
      setActiveTab('setup')
      setDeleteTarget(null)
      setSuccess('Heartbeat task deleted.')
    },
  })

  const selectedToolCount = useMemo(() => draft?.selectedTools.size || 0, [draft])

  if (query.isLoading || !state) {
    return (
      <>
        <PageHeader title="Heartbeat" description="Schedule safe background tasks" />
        <Loading />
      </>
    )
  }

  const capabilities = state.capabilities || []
  const updateDraft = (changes: Partial<TaskDraft>) =>
    setDraft((current) => (current ? { ...current, ...changes } : current))

  const selectDraft = (value: TaskDraft) => {
    setSuccess('')
    setDraft(value)
    setActiveTab('setup')
    setSearchParams({ task: value.id ? String(value.id) : 'new' })
  }

  const setAgent = (agent: HeartbeatCapability, checked: boolean) => {
    if (!draft) return
    const key = capabilityKey(agent)
    const agents = new Set(draft.selectedAgents)
    const selectedTools = new Set(draft.selectedTools)
    if (checked) {
      agents.add(key)
      agent.tools
        .filter((tool) => !tool.requires_confirmation)
        .forEach((tool) => selectedTools.add(toolKey(key, tool.name)))
    } else {
      agents.delete(key)
      for (const selected of selectedTools) {
        if (selected.startsWith(`${key}::`)) selectedTools.delete(selected)
      }
    }
    updateDraft({ selectedAgents: agents, selectedTools })
  }

  const setTool = (agentKey: string, name: string, checked: boolean) => {
    if (!draft) return
    const selectedTools = new Set(draft.selectedTools)
    checked
      ? selectedTools.add(toolKey(agentKey, name))
      : selectedTools.delete(toolKey(agentKey, name))
    updateDraft({ selectedTools })
  }

  const error = save.error || run.error || remove.error

  return (
    <>
      <PageHeader
        title={draft ? (draft.id ? 'Edit heartbeat' : 'Create heartbeat') : 'Heartbeat'}
        description={
          draft
            ? 'Configure the task, its agents, and notification delivery'
            : 'Choose a heartbeat to view or edit it'
        }
        actions={
          draft ? (
            <Button
              icon={<ArrowLeft size={14} />}
              onClick={() => {
                setDraft(undefined)
                setSuccess('')
                setSearchParams({})
              }}
            >
              Back to tasks
            </Button>
          ) : (
            <Button icon={<Plus size={14} />} onClick={() => selectDraft(newTask())}>
              New task
            </Button>
          )
        }
      />
      <div className={`page-content heartbeat-workspace ${draft ? 'is-editing' : 'is-listing'}`}>
        {!draft && (
          <div className="heartbeat-task-list">
            <div className="heartbeat-task-list__body">
              {!state.tasks.length && (
                <div className="heartbeat-empty-list">
                  <Bell size={22} />
                  <strong>No heartbeats yet</strong>
                  <small>Create a task for Mounir to run automatically.</small>
                  <Button icon={<Plus size={14} />} onClick={() => selectDraft(newTask())}>
                    Create heartbeat
                  </Button>
                </div>
              )}
              {state.tasks.map((task) => (
                <button
                  className="heartbeat-task-item"
                  type="button"
                  key={task.id}
                  onClick={() => selectDraft(taskDraft(task))}
                >
                  <span>
                    <strong>{task.name}</strong>
                    <small className="heartbeat-task-item__meta">
                      <span>
                        <Clock size={12} /> Every {intervalLabel(task.interval_minutes)}
                      </span>
                      <span>
                        <History size={12} />{' '}
                        {task.execution_limit === -1
                          ? 'Always runs'
                          : `${task.remaining_runs} run${task.remaining_runs === 1 ? '' : 's'} remaining`}
                      </span>
                      <span>
                        <Users size={12} /> {task.selected_agents.length} agent
                        {task.selected_agents.length === 1 ? '' : 's'}
                      </span>
                    </small>
                    <span className="heartbeat-task-item__badges">
                      {task.notify_telegram && (
                        <span className="heartbeat-channel-badge heartbeat-channel-badge--telegram">
                          Telegram
                        </span>
                      )}
                      {task.notify_whatsapp && (
                        <span className="heartbeat-channel-badge heartbeat-channel-badge--whatsapp">
                          WhatsApp
                        </span>
                      )}
                    </span>
                  </span>
                  <Status
                    value={task.enabled ? 'connected' : 'paused'}
                    label={task.enabled ? 'Working' : 'Paused'}
                  />
                </button>
              ))}
            </div>
          </div>
        )}

        {draft && (
          <div className="heartbeat-editor">
            <Card
              className="heartbeat-editor-card"
              title={draft.id ? draft.name || 'Heartbeat task' : 'New task'}
              description={
                draft.enabled ? `Runs every ${intervalLabel(draft.interval_minutes)}` : 'Paused'
              }
              action={
                <div className="heartbeat-editor__actions">
                  {draft.id && (
                    <Button
                      variant="ghost"
                      icon={<Trash2 size={14} />}
                      aria-label="Delete task"
                      onClick={() => {
                        const task = state.tasks.find((item) => item.id === draft.id)
                        if (task) setDeleteTarget(task)
                      }}
                    >
                      <span className="heartbeat-action-label">Delete</span>
                    </Button>
                  )}
                  <Button
                    icon={<Play size={14} />}
                    busy={run.isPending}
                    disabled={save.isPending || draft.remaining_runs === 0}
                    onClick={() => {
                      setSuccess('')
                      run.mutate(draft)
                    }}
                  >
                    Run now
                  </Button>
                  <Button
                    variant="primary"
                    icon={<Save size={14} />}
                    busy={save.isPending}
                    disabled={run.isPending}
                    onClick={() => {
                      setSuccess('')
                      save.mutate(draft)
                    }}
                  >
                    Save
                  </Button>
                </div>
              }
            >
              <div className="heartbeat-status-row">
                <span>
                  <strong>Automatic schedule</strong>
                  <small>
                    {draft.enabled
                      ? 'Mounir will run this task automatically.'
                      : 'You can still run it manually.'}
                  </small>
                </span>
                <Switch
                  checked={draft.enabled}
                  onChange={(enabled) => updateDraft({ enabled })}
                  label="Enable heartbeat task"
                />
              </div>

              <SectionTabs
                className="heartbeat-tabs"
                label="Heartbeat task sections"
                value={activeTab}
                options={[
                  { id: 'setup', label: 'Setup', icon: <Bell size={14} /> },
                  {
                    id: 'access',
                    label: 'Agents & tools',
                    icon: <Users size={14} />,
                    count: draft.selectedAgents.size,
                  },
                  {
                    id: 'history',
                    label: 'History',
                    icon: <History size={14} />,
                    count: draft.recent_runs.length || undefined,
                  },
                ]}
                onChange={(value) => setActiveTab(value as EditorTab)}
              />

              {activeTab === 'setup' && (
                <div className="heartbeat-tab-content heartbeat-setup">
                  <div className="form-grid">
                    <Field full label="Task name">
                      <input
                        maxLength={120}
                        value={draft.name}
                        onChange={(event) => updateDraft({ name: event.target.value })}
                        placeholder="Monitor important email"
                      />
                    </Field>
                    <Field full label="How often?">
                      <select
                        value={draft.interval_minutes}
                        onChange={(event) =>
                          updateDraft({ interval_minutes: Number(event.target.value) })
                        }
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
                    <div className="field field--full">
                      <span className="field__label">Run limit</span>
                      <div className="heartbeat-run-limit-controls">
                        <div className="heartbeat-run-limit">
                          <input
                            type="checkbox"
                            aria-label="Run forever"
                            checked={draft.execution_limit === -1}
                            onChange={(event) => {
                              updateDraft(
                                event.target.checked
                                  ? { execution_limit: -1, remaining_runs: -1 }
                                  : draft.saved_execution_limit === -1
                                    ? { execution_limit: 1, remaining_runs: 1 }
                                    : {
                                        execution_limit: draft.saved_execution_limit,
                                        remaining_runs: draft.saved_remaining_runs,
                                      },
                              )
                              if (!event.target.checked) {
                                window.requestAnimationFrame(() =>
                                  runLimitInputRef.current?.focus(),
                                )
                              }
                            }}
                          />
                          <span>Run forever</span>
                        </div>
                        {draft.execution_limit !== -1 && (
                          <input
                            type="number"
                            ref={runLimitInputRef}
                            min={1}
                            max={10000}
                            step={1}
                            value={draft.execution_limit}
                            aria-label="Number of executions"
                            onChange={(event) => {
                              const execution_limit = Number(event.target.value)
                              updateDraft({
                                execution_limit,
                                remaining_runs:
                                  execution_limit === draft.saved_execution_limit
                                    ? draft.saved_remaining_runs
                                    : execution_limit,
                              })
                            }}
                          />
                        )}
                      </div>
                      <span className="field__hint">
                        {draft.execution_limit === -1
                          ? 'Runs continuously until paused.'
                          : `Remaining executions: ${draft.remaining_runs}`}
                      </span>
                    </div>
                    <Field
                      full
                      label="What should Mounir do?"
                      hint="Describe the exact result Mounir should check for or produce, including when it should notify you and what information the alert should contain."
                    >
                      <textarea
                        rows={6}
                        maxLength={4000}
                        value={draft.instructions}
                        onChange={(event) => updateDraft({ instructions: event.target.value })}
                        placeholder="Check for unread messages from clients and notify me only when a reply needs my attention."
                      />
                    </Field>
                  </div>
                  <section className="heartbeat-delivery">
                    <div className="heartbeat-section-heading">
                      <div>
                        <strong>Send notifications to</strong>
                        <small>Alerts always appear in the app.</small>
                      </div>
                    </div>
                    <div className="heartbeat-channel-list">
                      <div>
                        <span>
                          <strong>Telegram</strong>
                          <small>Paired chat</small>
                        </span>
                        <Switch
                          checked={draft.notify_telegram}
                          onChange={(notify_telegram) => updateDraft({ notify_telegram })}
                          label="Telegram notifications"
                        />
                      </div>
                      <div>
                        <span>
                          <strong>WhatsApp</strong>
                          <small>Paired phone</small>
                        </span>
                        <Switch
                          checked={draft.notify_whatsapp}
                          onChange={(notify_whatsapp) => updateDraft({ notify_whatsapp })}
                          label="WhatsApp notifications"
                        />
                      </div>
                    </div>
                  </section>
                </div>
              )}

              {activeTab === 'access' && (
                <div className="heartbeat-tab-content">
                  <div className="heartbeat-section-heading">
                    <div>
                      <strong>Who can work on this task?</strong>
                      <small>
                        Select agents, then keep only the tools they need. Approval-required actions
                        are excluded.
                      </small>
                    </div>
                    <span className="heartbeat-selection-summary">
                      {draft.selectedAgents.size} agents · {selectedToolCount} tools
                    </span>
                  </div>
                  <div className="heartbeat-agent-list">
                    {capabilities.map((agent) => {
                      const key = capabilityKey(agent)
                      const selected = draft.selectedAgents.has(key)
                      const safeTools = agent.tools.filter((tool) => !tool.requires_confirmation)
                      const protectedCount = agent.tools.length - safeTools.length
                      return (
                        <section
                          className={`heartbeat-agent-row ${selected ? 'is-selected' : ''} ${safeTools.length ? '' : 'is-unavailable'}`}
                          key={key}
                        >
                          <label className="heartbeat-agent-row__header">
                            <input
                              type="checkbox"
                              disabled={!safeTools.length && !selected}
                              checked={selected}
                              onChange={(event) => setAgent(agent, event.target.checked)}
                            />
                            <span>
                              <strong>{agent.name}</strong>
                              <small>{agent.description || 'Specialist agent'}</small>
                            </span>
                            <em>
                              {safeTools.length
                                ? `${safeTools.length} safe tool${safeTools.length === 1 ? '' : 's'}`
                                : 'No safe tools'}
                            </em>
                          </label>
                          {selected && (
                            <div className="heartbeat-safe-tools">
                              {safeTools.map((tool) => (
                                <label key={tool.name}>
                                  <input
                                    type="checkbox"
                                    checked={draft.selectedTools.has(toolKey(key, tool.name))}
                                    onChange={(event) =>
                                      setTool(key, tool.name, event.target.checked)
                                    }
                                  />
                                  <span>
                                    <strong>{tool.label || readable(tool.name)}</strong>
                                    {(tool.server_name || tool.description) && (
                                      <small>
                                        {tool.server_name
                                          ? `${tool.server_name}${tool.description ? ` · ${tool.description}` : ''}`
                                          : tool.description}
                                      </small>
                                    )}
                                  </span>
                                </label>
                              ))}
                              {protectedCount > 0 && (
                                <p>
                                  {protectedCount} approval-required tool
                                  {protectedCount === 1 ? '' : 's'} excluded.
                                </p>
                              )}
                            </div>
                          )}
                        </section>
                      )
                    })}
                  </div>
                </div>
              )}

              {activeTab === 'history' && (
                <div className="heartbeat-tab-content">
                  <div className="heartbeat-section-heading">
                    <div>
                      <strong>Recent activity</strong>
                      <small>Only runs for this task appear here.</small>
                    </div>
                  </div>
                  <div className="run-list heartbeat-run-list">
                    {!draft.recent_runs.length && (
                      <div className="empty-state">This task has not run yet.</div>
                    )}
                    {draft.recent_runs.map((item) => (
                      <div className="run-row" key={item.id}>
                        <Status value={item.status} />
                        <p>
                          {item.message ||
                            item.summary ||
                            item.error ||
                            `${item.trigger || 'Scheduled'} run`}
                        </p>
                        <time>
                          {item.started_at ? new Date(item.started_at).toLocaleString() : ''}
                        </time>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          </div>
        )}
        <Feedback
          message={error instanceof Error ? error.message : success}
          kind={success ? 'success' : 'error'}
        />
      </div>
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete heartbeat task?"
        message={`Delete “${deleteTarget?.name || ''}” permanently? Its saved notifications remain in notification history.`}
        confirmLabel="Delete task"
        danger
        busy={remove.isPending}
        error={remove.error instanceof Error ? remove.error.message : ''}
        onConfirm={() => deleteTarget && remove.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  )
}
