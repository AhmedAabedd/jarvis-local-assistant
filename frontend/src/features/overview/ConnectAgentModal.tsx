import { Check, ChevronLeft, ChevronRight, Copy, Plus, RefreshCw, Search } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import type { McpServer, ModelRecord, Subagent } from '../../api/types'
import { api } from '../../api/client'
import { AutoTextarea } from '../../components/ui/AutoTextarea'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { Modal } from '../../components/ui/Modal'
import { stringList, toDataUrl } from '../resources/helpers'
import { ToolChoices } from '../resources/ToolChoices'

type WizardStep = 1 | 2 | 3
type ConfirmationMode = 'all' | 'selected' | 'none'

type Draft = {
  name: string
  description: string
  systemPrompt: string
  modelId: number
  serverId: number
  iconData?: string
  sourceIconId?: number
  enabled: boolean
}

const blankDraft = (): Draft => ({
  name: '',
  description: '',
  systemPrompt: '',
  modelId: 0,
  serverId: 0,
  enabled: true,
})

function uniqueCopyName(source: Subagent, agents: Subagent[]) {
  const slug = (name: string) =>
    name
      .trim()
      .toLocaleLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
  const names = new Set(agents.map((agent) => slug(agent.name)))
  let name = `${source.name} copy`
  let suffix = 2
  while (names.has(slug(name))) {
    name = `${source.name} copy ${suffix}`
    suffix += 1
  }
  return name
}

function confirmationMode(agent?: Subagent): ConfirmationMode {
  if (!agent) return 'all'
  const rules = stringList(agent.confirm_tools, agent.confirm_tool_calls ? ['*'] : [])
  return rules.includes('*') ? 'all' : rules.length ? 'selected' : 'none'
}

async function iconAsDataUrl(agentId: number, signal: AbortSignal) {
  const response = await fetch(`/api/subagents/${agentId}/icon`, { signal })
  if (!response.ok) throw new Error('Could not copy the subagent icon.')
  const blob = await response.blob()
  return toDataUrl(new File([blob], 'subagent-icon', { type: blob.type }))
}

export function ConnectAgentModal({
  open,
  parentAgentId,
  parentName,
  models,
  servers,
  agents,
  busy,
  error,
  onCreate,
  onClose,
}: {
  open: boolean
  parentAgentId: number | null
  parentName: string
  models: ModelRecord[]
  servers: McpServer[]
  agents: Subagent[]
  busy?: boolean
  error?: string
  onCreate: (body: object) => void
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [choosing, setChoosing] = useState(true)
  const [copyListOpen, setCopyListOpen] = useState(false)
  const [step, setStep] = useState<WizardStep>(1)
  const [furthestStep, setFurthestStep] = useState<WizardStep>(1)
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState<Draft>(blankDraft)
  const [source, setSource] = useState<Subagent | undefined>()
  const [selectedTools, setSelectedTools] = useState(new Set<string>())
  const [toolDefaults, setToolDefaults] = useState<string[] | null>(null)
  const [toolSelectionExplicit, setToolSelectionExplicit] = useState(false)
  const [initializedServerId, setInitializedServerId] = useState(0)
  const [confirmMode, setConfirmMode] = useState<ConfirmationMode>('all')
  const [confirmed, setConfirmed] = useState(new Set<string>())
  const [deduped, setDeduped] = useState(new Set<string>())
  const [localError, setLocalError] = useState('')
  const [copyingIcon, setCopyingIcon] = useState(false)

  const tools = useQuery({
    queryKey: ['server-tools', draft.serverId],
    queryFn: () => api.servers.tools(draft.serverId),
    enabled: open && !choosing && Boolean(draft.serverId),
  })
  const refreshTools = useMutation({
    mutationFn: () => api.servers.test(draft.serverId),
    onSuccess: (state) => queryClient.setQueryData(['server-tools', draft.serverId], state),
  })
  const availableTools = tools.data?.tools || []
  const availableNames = useMemo(() => availableTools.map((tool) => tool.name), [availableTools])
  const enabledTools = useMemo(
    () => availableTools.filter((tool) => selectedTools.has(tool.name)),
    [availableTools, selectedTools],
  )
  const enabledNames = useMemo(() => enabledTools.map((tool) => tool.name), [enabledTools])
  const visibleAgents = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    return agents.filter(
      (agent) =>
        !normalized ||
        agent.name.toLocaleLowerCase().includes(normalized) ||
        agent.description.toLocaleLowerCase().includes(normalized),
    )
  }, [agents, query])

  useEffect(() => {
    if (!open) return
    setChoosing(true)
    setCopyListOpen(false)
    setStep(1)
    setFurthestStep(1)
    setQuery('')
    setDraft(blankDraft())
    setSource(undefined)
    setSelectedTools(new Set())
    setToolDefaults(null)
    setToolSelectionExplicit(false)
    setInitializedServerId(0)
    setConfirmMode('all')
    setConfirmed(new Set())
    setDeduped(new Set())
    setLocalError('')
    setCopyingIcon(false)
  }, [open, parentAgentId])

  useEffect(() => {
    if (!draft.serverId || tools.data === undefined) return
    if (initializedServerId !== draft.serverId) {
      const defaults = toolDefaults === null ? availableNames : toolDefaults
      setSelectedTools(new Set(defaults.filter((name) => availableNames.includes(name))))
      setInitializedServerId(draft.serverId)
      return
    }
    if (
      !toolSelectionExplicit &&
      (selectedTools.size !== availableNames.length ||
        availableNames.some((name) => !selectedTools.has(name)))
    ) {
      setSelectedTools(new Set(availableNames))
    }
  }, [
    availableNames,
    draft.serverId,
    initializedServerId,
    selectedTools,
    toolDefaults,
    toolSelectionExplicit,
    tools.data,
  ])

  useEffect(() => {
    if (!source?.has_icon || draft.iconData !== undefined) return
    const controller = new AbortController()
    setCopyingIcon(true)
    iconAsDataUrl(source.id, controller.signal)
      .then((iconData) => setDraft((current) => ({ ...current, iconData })))
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setLocalError(cause instanceof Error ? cause.message : 'Could not copy the icon.')
      })
      .finally(() => setCopyingIcon(false))
    return () => controller.abort()
  }, [draft.iconData, source])

  const start = (agent?: Subagent) => {
    const confirmRules = stringList(agent?.confirm_tools, agent?.confirm_tool_calls ? ['*'] : [])
    setSource(agent)
    setDraft(
      agent
        ? {
            name: uniqueCopyName(agent, agents),
            description: agent.description,
            systemPrompt: agent.system_prompt || '',
            modelId: Number(agent.model_id),
            serverId: Number(agent.mcp_server_id),
            sourceIconId: agent.has_icon ? agent.id : undefined,
            enabled: !(agent.enabled === false || Number(agent.enabled) === 0),
          }
        : blankDraft(),
    )
    setToolDefaults(agent?.enabled_tools ?? null)
    setToolSelectionExplicit(agent?.enabled_tools != null)
    setInitializedServerId(0)
    setSelectedTools(new Set())
    setConfirmMode(confirmationMode(agent))
    setConfirmed(new Set(confirmRules.filter((name) => name !== '*')))
    setDeduped(new Set(stringList(agent?.dedupe_tools)))
    setLocalError('')
    setStep(1)
    setFurthestStep(1)
    setChoosing(false)
    setCopyListOpen(false)
  }

  const setServer = (serverId: number) => {
    setDraft((current) => ({ ...current, serverId }))
    setToolDefaults(null)
    setToolSelectionExplicit(false)
    setInitializedServerId(0)
    setSelectedTools(new Set())
    setConfirmMode('all')
    setConfirmed(new Set())
    setDeduped(new Set())
  }

  const goToStep = (target: WizardStep) => {
    setLocalError('')
    if (target > 1) {
      if (!draft.name.trim()) return setLocalError('Enter a name for the subagent.')
      if (!draft.description.trim()) return setLocalError('Enter a description for the subagent.')
      if (!draft.modelId) return setLocalError('Choose a model for the subagent.')
      if (!draft.serverId) return setLocalError('Choose an MCP server for the subagent.')
    }
    setStep(target)
    setFurthestStep((current) => Math.max(current, target) as WizardStep)
  }

  const next = () => goToStep((step + 1) as WizardStep)

  const createSubagent = () => {
    setLocalError('')
    const confirmedTools = [...confirmed].filter((name) => enabledNames.includes(name))
    const dedupedTools = [...deduped].filter((name) => enabledNames.includes(name))
    if (confirmMode === 'selected' && !confirmedTools.length) {
      setLocalError('Select at least one action requiring confirmation.')
      return
    }
    const allToolsEnabled =
      availableNames.length > 0 &&
      availableNames.length === selectedTools.size &&
      availableNames.every((name) => selectedTools.has(name))
    onCreate({
      name: draft.name.trim(),
      description: draft.description.trim(),
      system_prompt: draft.systemPrompt.trim(),
      model_id: draft.modelId,
      mcp_server_id: draft.serverId,
      parent_agent_id: parentAgentId,
      enabled_tools: !toolSelectionExplicit || allToolsEnabled ? null : [...selectedTools],
      confirm_tools:
        confirmMode === 'all' ? ['*'] : confirmMode === 'selected' ? confirmedTools : [],
      confirm_tool_calls: confirmMode !== 'none',
      dedupe_tools: dedupedTools,
      enabled: draft.enabled,
      ...(draft.iconData !== undefined ? { icon_data: draft.iconData } : {}),
    })
  }

  const goBack = () => {
    setLocalError('')
    if (step > 1) setStep((step - 1) as WizardStep)
    else {
      setChoosing(true)
      setCopyListOpen(Boolean(source))
    }
  }

  const footer = choosing ? (
    <>
      {copyListOpen && (
        <Button type="button" onClick={() => setCopyListOpen(false)}>
          <ChevronLeft size={13} /> Previous
        </Button>
      )}
      <Button type="button" onClick={onClose}>
        Cancel
      </Button>
    </>
  ) : (
    <>
      <Button type="button" onClick={goBack} disabled={busy}>
        <ChevronLeft size={13} /> Previous
      </Button>
      {step < 3 ? (
        <Button type="button" variant="primary" onClick={next} disabled={copyingIcon || busy}>
          Next <ChevronRight size={13} />
        </Button>
      ) : (
        <Button
          type="button"
          variant="primary"
          icon={<Plus size={14} />}
          onClick={createSubagent}
          busy={busy}
          disabled={copyingIcon}
        >
          Create subagent
        </Button>
      )}
    </>
  )

  return (
    <Modal
      open={open}
      wide
      title={
        choosing
          ? copyListOpen
            ? 'Copy existing subagent'
            : 'Add subagent'
          : source
            ? `Copy ${source.name}`
            : 'Create subagent'
      }
      description={
        choosing
          ? copyListOpen
            ? 'Choose the subagent whose configuration you want to reuse.'
            : `Choose how to add a specialist under ${parentName}.`
          : `This independent subagent will be added under ${parentName}.`
      }
      onClose={onClose}
      footer={footer}
    >
      {choosing ? (
        <div className="subagent-start">
          {copyListOpen ? (
            <>
              <label className="subagent-start__search">
                <Search size={14} />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search subagents…"
                  autoFocus
                />
              </label>
              <div className="subagent-start__list">
                {visibleAgents.length ? (
                  visibleAgents.map((agent) => (
                    <button
                      className="subagent-start__agent"
                      type="button"
                      key={agent.id}
                      onClick={() => start(agent)}
                    >
                      <span className={`avatar ${agent.has_icon ? 'has-image' : ''}`}>
                        {agent.has_icon ? (
                          <img src={`/api/subagents/${agent.id}/icon`} alt="" />
                        ) : (
                          agent.name.charAt(0).toUpperCase()
                        )}
                      </span>
                      <span>
                        <strong>{agent.name}</strong>
                        <small>{agent.description}</small>
                      </span>
                    </button>
                  ))
                ) : (
                  <div className="empty-state subagent-start__empty">
                    {query ? 'No matching subagents.' : 'No existing subagents yet.'}
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="subagent-start__choices">
              <button className="subagent-start__choice" type="button" onClick={() => start()}>
                <span className="subagent-start__choice-title">
                  <Plus size={18} />
                  <strong>Create new subagent</strong>
                </span>
                <small>Start with a clean configuration.</small>
              </button>
              <button
                className="subagent-start__choice"
                type="button"
                onClick={() => {
                  setQuery('')
                  setCopyListOpen(true)
                }}
              >
                <span className="subagent-start__choice-title">
                  <Copy size={17} />
                  <strong>Copy existing subagent</strong>
                </span>
                <small>Reuse an existing configuration as an independent subagent.</small>
              </button>
            </div>
          )}
        </div>
      ) : (
        <form onSubmit={(event) => event.preventDefault()}>
          <nav className="subagent-wizard__steps" aria-label="Creation progress">
            {[
              { number: 1, label: 'Configuration' },
              { number: 2, label: 'Tools' },
              { number: 3, label: 'Security' },
            ].map(({ number, label }) => (
              <button
                type="button"
                className={`subagent-wizard__step ${step === number ? 'is-current' : ''} ${furthestStep > number ? 'is-complete' : ''}`}
                key={number}
                aria-current={step === number ? 'step' : undefined}
                onClick={() => goToStep(number as WizardStep)}
              >
                <span className="subagent-wizard__step-marker">
                  {furthestStep > number ? <Check size={13} /> : number}
                </span>
                <small>{label}</small>
              </button>
            ))}
          </nav>

          {step === 1 && (
            <div className="form-grid subagent-wizard__page">
              {!models.length && <div className="guidance">Create a model first.</div>}
              {!servers.length && <div className="guidance">Connect an MCP server first.</div>}
              <Field full label="Name" hint="Use a unique name for this specific role.">
                <input
                  value={draft.name}
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                  autoFocus
                />
              </Field>
              <Field full label="Icon" hint="Square PNG, JPEG, WebP, or GIF smaller than 512 KB.">
                <div className="subagent-wizard__icon-field">
                  <span
                    className={`avatar ${draft.iconData || draft.sourceIconId ? 'has-image' : ''}`}
                  >
                    {draft.iconData ? (
                      <img src={draft.iconData} alt="Preview" />
                    ) : draft.sourceIconId ? (
                      <img src={`/api/subagents/${draft.sourceIconId}/icon`} alt="Current" />
                    ) : (
                      (draft.name || '?').charAt(0).toUpperCase()
                    )}
                  </span>
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    onChange={async (event) => {
                      const file = event.target.files?.[0]
                      if (!file) return
                      if (file.size > 512 * 1024) {
                        setLocalError('The icon must be smaller than 512 KB.')
                        return
                      }
                      setDraft({
                        ...draft,
                        iconData: await toDataUrl(file),
                        sourceIconId: undefined,
                      })
                    }}
                  />
                  {(draft.iconData || draft.sourceIconId) && (
                    <Button
                      type="button"
                      onClick={() => setDraft({ ...draft, iconData: '', sourceIconId: undefined })}
                    >
                      Remove
                    </Button>
                  )}
                </div>
              </Field>
              <Field
                full
                label="Description"
                hint="Explain exactly when the parent should delegate here."
              >
                <AutoTextarea
                  value={draft.description}
                  onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                  rows={2}
                />
              </Field>
              <Field
                full
                label="System prompt"
                hint="Instructions that apply only to this subagent."
              >
                <AutoTextarea
                  value={draft.systemPrompt}
                  onChange={(event) => setDraft({ ...draft, systemPrompt: event.target.value })}
                  rows={5}
                />
              </Field>
              <Field label="Model">
                <select
                  value={draft.modelId || ''}
                  onChange={(event) => setDraft({ ...draft, modelId: Number(event.target.value) })}
                >
                  <option value="">Choose a model…</option>
                  {models.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.name} — {model.provider}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="MCP server">
                <select
                  value={draft.serverId || ''}
                  onChange={(event) => setServer(Number(event.target.value))}
                >
                  <option value="">Choose a server…</option>
                  {servers.map((server) => (
                    <option key={server.id} value={server.id}>
                      {server.name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          )}

          {step === 2 && (
            <div className="subagent-wizard__page">
              <div className="subagent-wizard__section-heading">
                <span>
                  <strong>Available tools</strong>
                  <small>Choose which MCP actions this subagent can use.</small>
                </span>
                <div>
                  <button
                    type="button"
                    onClick={() => {
                      setToolSelectionExplicit(false)
                      setSelectedTools(new Set(availableNames))
                    }}
                  >
                    Enable all
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setToolSelectionExplicit(true)
                      setSelectedTools(new Set())
                    }}
                  >
                    Disable all
                  </button>
                  <button
                    type="button"
                    onClick={() => refreshTools.mutate()}
                    disabled={refreshTools.isPending}
                  >
                    <RefreshCw size={12} /> Refresh
                  </button>
                </div>
              </div>
              {tools.isLoading ? (
                <Loading label="Loading tools…" />
              ) : (
                <ToolChoices
                  tools={availableTools}
                  selected={selectedTools}
                  onChange={(selection) => {
                    setToolSelectionExplicit(true)
                    setSelectedTools(selection)
                  }}
                />
              )}
            </div>
          )}

          {step === 3 && (
            <div className="form-grid subagent-wizard__page">
              <Field
                full
                label="Action confirmation"
                hint="Control which actions require your approval before they run."
              >
                <select
                  value={confirmMode}
                  onChange={(event) => setConfirmMode(event.target.value as ConfirmationMode)}
                >
                  <option value="all">Ask before every action</option>
                  <option value="selected">Ask for selected actions</option>
                  <option value="none">Run without confirmation</option>
                </select>
              </Field>
              {confirmMode === 'selected' && (
                <div className="key-value-editor">
                  <div className="key-value-editor__title">
                    <span>
                      <strong>Actions requiring confirmation</strong>
                      <small>Select the actions Mounir must ask you to approve.</small>
                    </span>
                  </div>
                  <ToolChoices
                    tools={enabledTools}
                    selected={confirmed}
                    onChange={setConfirmed}
                    empty="Enable at least one tool before configuring confirmation rules."
                  />
                </div>
              )}
              <div className="key-value-editor">
                <div className="key-value-editor__title">
                  <span>
                    <strong>Repeated action protection</strong>
                    <small>
                      Block an identical action from running twice during the same request.
                    </small>
                  </span>
                </div>
                <ToolChoices
                  tools={enabledTools}
                  selected={deduped}
                  onChange={setDeduped}
                  empty="Enable at least one tool before configuring repeated-action protection."
                />
              </div>
            </div>
          )}

          <Feedback
            message={
              localError ||
              error ||
              (tools.error instanceof Error ? tools.error.message : '') ||
              (refreshTools.error instanceof Error ? refreshTools.error.message : '')
            }
          />
        </form>
      )}
    </Modal>
  )
}
