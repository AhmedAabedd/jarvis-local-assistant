import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, ChevronLeft, Clock, Edit3, History, Play, Plus, Trash2, Users } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import type { HeartbeatSettings, HeartbeatTask } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Loading } from '../../components/ui/Loading'
import { SectionTabs } from '../../components/ui/SectionTabs'
import { Status } from '../../components/ui/Status'
import { Switch } from '../../components/ui/Switch'
import { keys } from '../../hooks/useStudioData'
import { readable } from '../resources/helpers'
import { PageHeader } from '../studio/PageHeader'
import { formatHeartbeatInterval, HeartbeatTaskWizard } from './HeartbeatTaskWizard'

type DetailTab = 'setup' | 'access' | 'history'

function Detail({
  label,
  value,
  full = false,
}: {
  label: string
  value: string | number
  full?: boolean
}) {
  return (
    <dl className={`detail ${full ? 'detail--full' : ''}`}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </dl>
  )
}

function dateLabel(value?: string | null) {
  return value ? new Date(value).toLocaleString() : 'Not scheduled'
}

function runLimitLabel(task: HeartbeatTask) {
  if (task.execution_limit === -1) return 'Run forever'
  return `${task.remaining_runs} of ${task.execution_limit} remaining`
}

export function HeartbeatPage() {
  const client = useQueryClient()
  const query = useQuery({ queryKey: keys.heartbeat, queryFn: api.heartbeat.get })
  const state = query.data
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedId = Number(searchParams.get('task') || 0)
  const selected = state?.tasks.find((task) => task.id === selectedId)
  const [detailTab, setDetailTab] = useState<DetailTab>('setup')
  const [wizardTask, setWizardTask] = useState<HeartbeatTask | null | undefined>(undefined)
  const [success, setSuccess] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<HeartbeatTask | null>(null)

  useEffect(() => {
    if (selectedId && state && !selected) setSearchParams({})
  }, [selected, selectedId, setSearchParams, state])

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
    setSearchParams({ task: String(task.id) })
  }

  const save = useMutation({
    mutationFn: ({ task, payload }: { task?: HeartbeatTask; payload: object }) =>
      task ? api.heartbeat.updateTask(task.id, payload) : api.heartbeat.createTask(payload),
    onSuccess: (task) => {
      syncTask(task)
      setWizardTask(undefined)
      setSuccess(task.id === wizardTask?.id ? 'Heartbeat task updated.' : 'Heartbeat task created.')
    },
  })

  const run = useMutation({
    mutationFn: (id: number) => api.heartbeat.runTask(id),
    onSuccess: (task) => {
      syncTask(task)
      setSuccess('Heartbeat task completed.')
    },
  })

  const toggleSchedule = useMutation({
    mutationFn: ({ task, enabled }: { task: HeartbeatTask; enabled: boolean }) =>
      api.heartbeat.updateTask(task.id, { enabled }),
    onSuccess: (task) => {
      syncTask(task)
      setSuccess(task.enabled ? 'Heartbeat task enabled.' : 'Heartbeat task paused.')
    },
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.heartbeat.removeTask(id),
    onSuccess: (_result, id) => {
      client.setQueryData<HeartbeatSettings>(keys.heartbeat, (current) =>
        current ? { ...current, tasks: current.tasks.filter((task) => task.id !== id) } : current,
      )
      setSearchParams({})
      setDeleteTarget(null)
      setSuccess('Heartbeat task deleted.')
    },
  })

  const capabilityMap = useMemo(
    () =>
      new Map(
        (state?.capabilities || []).map((capability) => [
          capability.key || capability.id || '',
          capability,
        ]),
      ),
    [state?.capabilities],
  )

  if (query.isLoading || !state) {
    return (
      <>
        <PageHeader title="Heartbeat" description="Schedule safe background tasks" />
        <Loading />
      </>
    )
  }

  const openCreate = () => {
    save.reset()
    setSuccess('')
    setWizardTask(null)
  }

  const openEdit = () => {
    if (!selected) return
    save.reset()
    setSuccess('')
    setWizardTask(selected)
  }

  const openTask = (task: HeartbeatTask) => {
    setSuccess('')
    setDetailTab('setup')
    setSearchParams({ task: String(task.id) })
  }

  const headerActions = selected ? (
    <div className="resource-header-actions">
      <Button
        className="resource-header-action"
        icon={<Trash2 size={15} />}
        onClick={() => setDeleteTarget(selected)}
        aria-label="Delete heartbeat task"
        title="Delete heartbeat task"
      />
      <Button
        className="resource-header-action"
        variant="primary"
        icon={<Edit3 size={15} />}
        onClick={openEdit}
        aria-label="Edit heartbeat task"
        title="Edit heartbeat task"
      />
    </div>
  ) : (
    <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
      New task
    </Button>
  )

  return (
    <>
      <PageHeader
        title={selected ? selected.name : 'Heartbeat'}
        description={
          selected ? 'Saved heartbeat task configuration' : 'Schedule safe background tasks'
        }
        leading={
          selected ? (
            <Button
              icon={<ChevronLeft size={15} />}
              onClick={() => setSearchParams({})}
              aria-label="Back to heartbeat tasks"
              title="Back to heartbeat tasks"
            />
          ) : undefined
        }
        actions={headerActions}
      />

      <div className={`page-content heartbeat-workspace ${selected ? 'is-editing' : 'is-listing'}`}>
        {!selected && (
          <div className="heartbeat-task-list">
            <div className="heartbeat-task-list__body">
              {!state.tasks.length && (
                <div className="heartbeat-empty-list">
                  <Bell size={22} />
                  <strong>No heartbeats yet</strong>
                  <small>Create a task for Mounir to run automatically.</small>
                  <Button icon={<Plus size={14} />} onClick={openCreate}>
                    Create heartbeat
                  </Button>
                </div>
              )}
              {state.tasks.map((task) => (
                <button
                  className="heartbeat-task-item"
                  type="button"
                  key={task.id}
                  onClick={() => openTask(task)}
                >
                  <span>
                    <strong>{task.name}</strong>
                    <small className="heartbeat-task-item__meta">
                      <span>
                        <Clock size={12} /> Every {formatHeartbeatInterval(task.interval_minutes)}
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

        {selected && (
          <section className="card resource-workspace resource-workspace--readonly heartbeat-readonly-card">
            <div className="setting-row readonly-setting-row heartbeat-readonly-summary">
              <span>
                <strong>Automatic schedule</strong>
                <small>
                  {selected.enabled
                    ? `Working · runs every ${formatHeartbeatInterval(selected.interval_minutes)}`
                    : 'Paused · you can still run it manually.'}
                </small>
              </span>
              <div className="heartbeat-readonly-summary__actions">
                <Switch
                  checked={selected.enabled}
                  disabled={toggleSchedule.isPending}
                  onChange={(enabled) => {
                    setSuccess('')
                    toggleSchedule.mutate({ task: selected, enabled })
                  }}
                  label={selected.enabled ? 'Pause heartbeat task' : 'Enable heartbeat task'}
                />
                <Button
                  icon={<Play size={14} />}
                  busy={run.isPending}
                  disabled={selected.remaining_runs === 0}
                  onClick={() => {
                    setSuccess('')
                    run.mutate(selected.id)
                  }}
                >
                  Run now
                </Button>
              </div>
            </div>
            <div className="card__body heartbeat-readonly-body">
              <SectionTabs
                className="heartbeat-tabs"
                label="Heartbeat task details"
                value={detailTab}
                options={[
                  { id: 'setup', label: 'Setup', icon: <Bell size={14} /> },
                  {
                    id: 'access',
                    label: 'Agents & tools',
                    icon: <Users size={14} />,
                    count: selected.selected_agents.length,
                  },
                  {
                    id: 'history',
                    label: 'History',
                    icon: <History size={14} />,
                    count: selected.recent_runs.length || undefined,
                  },
                ]}
                onChange={(value) => setDetailTab(value as DetailTab)}
              />

              {detailTab === 'setup' && (
                <div className="heartbeat-tab-content detail-grid heartbeat-readonly-setup">
                  <Detail label="Status" value={selected.enabled ? 'Working' : 'Paused'} />
                  <Detail
                    label="Schedule"
                    value={`Every ${formatHeartbeatInterval(selected.interval_minutes)}`}
                  />
                  <Detail label="Run limit" value={runLimitLabel(selected)} />
                  <Detail label="Next run" value={dateLabel(selected.next_run_at)} />
                  <Detail
                    label="Notifications"
                    value={[
                      'In-app',
                      selected.notify_telegram ? 'Telegram' : '',
                      selected.notify_whatsapp ? 'WhatsApp' : '',
                    ]
                      .filter(Boolean)
                      .join(', ')}
                    full
                  />
                  <Detail label="Prompt" value={selected.instructions} full />
                </div>
              )}

              {detailTab === 'access' && (
                <div className="heartbeat-tab-content heartbeat-readonly-access">
                  {!selected.selected_agents.length && (
                    <div className="empty-state">No agents are assigned to this task.</div>
                  )}
                  {selected.selected_agents.map((agentKey) => {
                    const capability = capabilityMap.get(agentKey)
                    const tools = selected.selected_tools.filter(
                      (tool) => tool.agent_key === agentKey,
                    )
                    return (
                      <div className="heartbeat-readonly-agent" key={agentKey}>
                        <span>
                          <strong>{capability?.name || agentKey}</strong>
                          <small>{capability?.description || 'Saved specialist agent'}</small>
                        </span>
                        <div className="chips">
                          {tools.map((tool) => {
                            const definition = capability?.tools.find(
                              (item) => item.name === tool.tool_name,
                            )
                            return (
                              <span className="chip" key={`${agentKey}:${tool.tool_name}`}>
                                {definition?.label || readable(tool.tool_name)}
                              </span>
                            )
                          })}
                          {!tools.length && <small>No tools selected</small>}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}

              {detailTab === 'history' && (
                <div className="heartbeat-tab-content">
                  <div className="run-list heartbeat-run-list">
                    {!selected.recent_runs.length && (
                      <div className="empty-state">This task has not run yet.</div>
                    )}
                    {selected.recent_runs.map((item) => (
                      <div className="run-row" key={item.id}>
                        <Status value={item.status} />
                        <p>
                          {item.message ||
                            item.summary ||
                            item.error ||
                            `${item.trigger || 'Scheduled'} run`}
                        </p>
                        <time>{dateLabel(item.started_at)}</time>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </section>
        )}

        <Feedback
          message={
            run.error instanceof Error
              ? run.error.message
              : toggleSchedule.error instanceof Error
                ? toggleSchedule.error.message
                : remove.error instanceof Error
                  ? remove.error.message
                  : success
          }
          kind={success ? 'success' : 'error'}
        />
      </div>

      <HeartbeatTaskWizard
        key={wizardTask === undefined ? 'closed' : wizardTask?.id || 'new'}
        open={wizardTask !== undefined}
        task={wizardTask || undefined}
        capabilities={state.capabilities || []}
        busy={save.isPending}
        requestError={save.error instanceof Error ? save.error.message : ''}
        onClose={() => setWizardTask(undefined)}
        onSubmit={async (payload) => {
          await save.mutateAsync({ task: wizardTask || undefined, payload })
        }}
      />

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
