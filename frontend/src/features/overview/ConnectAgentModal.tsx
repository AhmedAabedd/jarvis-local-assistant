import {
  Bot,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Eye,
  Plus,
  Search,
  Workflow as WorkflowIcon,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type {
  McpServer,
  ModelRecord,
  Subagent,
  SubagentMcpSource,
  SubagentPlacement,
  Workflow,
} from '../../api/types'
import { AutoTextarea } from '../../components/ui/AutoTextarea'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { readable, stringList, toDataUrl } from '../resources/helpers'
import {
  expandLegacyToolRules,
  MultiServerToolPicker,
  selectedToolOptions,
  useMcpToolCatalog,
} from '../resources/McpToolAccess'
import { ToolChoices } from '../resources/ToolChoices'

type WizardStep = 1 | 2 | 3
type ConfirmationMode = 'all' | 'selected' | 'none'

type Draft = {
  name: string
  description: string
  systemPrompt: string
  modelId: number
  iconData?: string
  sourceIconId?: number
  enabled: boolean
}

const blankDraft = (): Draft => ({
  name: '',
  description: '',
  systemPrompt: '',
  modelId: 0,
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

function AgentListItem({
  agent,
  disabledReason,
  busy,
  onSelect,
  onDetails,
}: {
  agent: Subagent
  disabledReason?: string
  busy?: boolean
  onSelect: () => void
  onDetails: () => void
}) {
  return (
    <div className="subagent-start__agent">
      <button
        className="subagent-start__agent-select"
        type="button"
        disabled={Boolean(disabledReason) || busy}
        onClick={onSelect}
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
          <small>{disabledReason || agent.description}</small>
        </span>
      </button>
      <button
        className="subagent-start__agent-details"
        type="button"
        onClick={onDetails}
        aria-label={`View ${agent.name} details`}
        title="View details"
      >
        <Eye size={14} />
      </button>
    </div>
  )
}

function PreviewValue({
  label,
  value,
  full,
}: {
  label: string
  value: string | number
  full?: boolean
}) {
  return (
    <div className={`detail ${full ? 'detail--full' : ''}`}>
      <dt>{label}</dt>
      <dd>{value || 'Not configured'}</dd>
    </div>
  )
}

function SubagentPreview({ agent }: { agent: Subagent }) {
  const sources = agent.mcp_sources || []
  const confirmationTools = stringList(agent.confirm_tools, agent.confirm_tool_calls ? ['*'] : [])
  const repeatedTools = stringList(agent.dedupe_tools)
  const placementCount = agent.placement_count || 0
  const modelLabel = agent.model_name
    ? `${agent.model_name}${agent.model ? ` — ${agent.model}` : ''}`
    : agent.model || 'Not configured'
  const chips = (values: string[], empty: string) => (
    <div className="chips">
      {values.length ? (
        values.map((name) => (
          <span className="chip" key={name}>
            {name === '*' ? 'Every action' : readable(name)}
          </span>
        ))
      ) : (
        <span className="chip">{empty}</span>
      )}
    </div>
  )
  return (
    <div className="subagent-preview">
      <div className="subagent-preview__identity">
        <span className={`avatar ${agent.has_icon ? 'has-image' : ''}`}>
          {agent.has_icon ? (
            <img src={`/api/subagents/${agent.id}/icon`} alt="" />
          ) : (
            agent.name.charAt(0).toUpperCase()
          )}
        </span>
        <span>
          <strong>{agent.enabled ? 'Active' : 'Inactive'}</strong>
          <small>
            {placementCount} workflow placement{placementCount === 1 ? '' : 's'}
          </small>
        </span>
      </div>
      <dl className="detail-grid subagent-preview__grid">
        <PreviewValue label="Model" value={modelLabel} />
        <PreviewValue
          label="Capabilities"
          value={
            sources.length
              ? `${sources.length} MCP server${sources.length === 1 ? '' : 's'}`
              : 'Prompt only'
          }
        />
        <PreviewValue label="Description" value={agent.description} full />
        <PreviewValue
          label="System prompt"
          value={agent.system_prompt || 'Default instructions'}
          full
        />
        <div className="detail detail--full">
          <dt>MCP sources</dt>
          <dd>
            {chips(
              sources.map((source) =>
                `${source.mcp_server_name || `Server ${source.mcp_server_id}`} · ${source.enabled_tools === null ? 'All tools' : `${source.enabled_tools.length} tools`}`,
              ),
              'No external tools',
            )}
          </dd>
        </div>
        <div className="detail detail--full">
          <dt>Actions requiring confirmation</dt>
          <dd>{chips(confirmationTools, 'None')}</dd>
        </div>
        <div className="detail detail--full">
          <dt>Repeated action protection</dt>
          <dd>{chips(repeatedTools, 'None')}</dd>
        </div>
      </dl>
    </div>
  )
}

export function ConnectAgentModal({
  open,
  parentNodeId,
  parentName,
  models,
  servers,
  agents,
  placements,
  workflows = [],
  currentWorkflowId,
  busy,
  error,
  onCreate,
  onConnect,
  onConnectWorkflow,
  onClose,
}: {
  open: boolean
  parentNodeId: number | null
  parentName: string
  models: ModelRecord[]
  servers: McpServer[]
  agents: Subagent[]
  placements: SubagentPlacement[]
  workflows?: Workflow[]
  currentWorkflowId?: number
  busy?: boolean
  error?: string
  onCreate: (body: object) => void
  onConnect: (subagentId: number) => void
  onConnectWorkflow?: (workflowId: number) => void
  onClose: () => void
}) {
  const [resourceKind, setResourceKind] = useState<'subagent' | 'workflow' | null>(null)
  const [libraryOpen, setLibraryOpen] = useState(true)
  const [choosing, setChoosing] = useState(true)
  const [copyListOpen, setCopyListOpen] = useState(false)
  const [step, setStep] = useState<WizardStep>(1)
  const [furthestStep, setFurthestStep] = useState<WizardStep>(1)
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState<Draft>(blankDraft)
  const [source, setSource] = useState<Subagent | undefined>()
  const [sources, setSources] = useState<SubagentMcpSource[]>([])
  const [confirmMode, setConfirmMode] = useState<ConfirmationMode>('all')
  const [confirmed, setConfirmed] = useState(new Set<string>())
  const [deduped, setDeduped] = useState(new Set<string>())
  const [localError, setLocalError] = useState('')
  const [copyingIcon, setCopyingIcon] = useState(false)
  const [previewAgent, setPreviewAgent] = useState<Subagent | null>(null)

  const toolGroups = useMcpToolCatalog(servers)
  const enabledTools = useMemo(() => selectedToolOptions(toolGroups, sources), [toolGroups, sources])
  const enabledNames = useMemo(() => enabledTools.map((tool) => tool.name), [enabledTools])
  const confirmedSelection = useMemo(
    () => expandLegacyToolRules([...confirmed], enabledTools),
    [confirmed, enabledTools],
  )
  const dedupedSelection = useMemo(
    () => expandLegacyToolRules([...deduped], enabledTools),
    [deduped, enabledTools],
  )
  const visibleAgents = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    return agents.filter(
      (agent) =>
        !normalized ||
        agent.name.toLocaleLowerCase().includes(normalized) ||
        agent.description.toLocaleLowerCase().includes(normalized),
    )
  }, [agents, query])
  const visibleWorkflows = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    return workflows.filter(
      (workflow) =>
        workflow.id !== currentWorkflowId &&
        (!normalized ||
          workflow.name.toLocaleLowerCase().includes(normalized) ||
          workflow.description.toLocaleLowerCase().includes(normalized)),
    )
  }, [currentWorkflowId, query, workflows])
  const unavailableReasons = useMemo(() => {
    const unavailable = new Map<number, string>()
    placements.forEach((placement) => {
      if (placement.parent_node_id === parentNodeId) {
        unavailable.set(placement.subagent_id, 'Already added here')
      }
    })
    let current = parentNodeId
    const visited = new Set<number>()
    while (current !== null && !visited.has(current)) {
      visited.add(current)
      const placement = placements.find((item) => item.node_id === current)
      if (!placement) break
      if (!unavailable.has(placement.subagent_id)) {
        unavailable.set(placement.subagent_id, 'Already in this branch')
      }
      current = placement.parent_node_id
    }
    return unavailable
  }, [parentNodeId, placements])

  useEffect(() => {
    if (!open) return
    setResourceKind(null)
    setLibraryOpen(true)
    setChoosing(true)
    setCopyListOpen(false)
    setStep(1)
    setFurthestStep(1)
    setQuery('')
    setDraft(blankDraft())
    setSource(undefined)
    setSources([])
    setConfirmMode('all')
    setConfirmed(new Set())
    setDeduped(new Set())
    setLocalError('')
    setCopyingIcon(false)
    setPreviewAgent(null)
  }, [open, parentNodeId])

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
            sourceIconId: agent.has_icon ? agent.id : undefined,
            enabled: !(agent.enabled === false || Number(agent.enabled) === 0),
          }
        : blankDraft(),
    )
    setSources(
      agent?.mcp_sources?.length
        ? agent.mcp_sources
        : agent?.mcp_server_id
          ? [{ mcp_server_id: Number(agent.mcp_server_id), enabled_tools: agent.enabled_tools }]
          : [],
    )
    setConfirmMode(confirmationMode(agent))
    setConfirmed(new Set(confirmRules.filter((name) => name !== '*')))
    setDeduped(new Set(stringList(agent?.dedupe_tools)))
    setLocalError('')
    setStep(1)
    setFurthestStep(1)
    setChoosing(false)
    setCopyListOpen(false)
  }

  const goToStep = (target: WizardStep) => {
    setLocalError('')
    if (target > 1) {
      if (!draft.name.trim()) return setLocalError('Enter a name for the subagent.')
      if (!draft.description.trim()) return setLocalError('Enter a description for the subagent.')
      if (!draft.modelId) return setLocalError('Choose a model for the subagent.')
    }
    setStep(target)
    setFurthestStep((current) => Math.max(current, target) as WizardStep)
  }

  const next = () => goToStep((step + 1) as WizardStep)

  const createSubagent = () => {
    setLocalError('')
    const confirmedTools = [...confirmedSelection].filter((name) => enabledNames.includes(name))
    const dedupedTools = [...dedupedSelection].filter((name) => enabledNames.includes(name))
    if (sources.length && confirmMode === 'selected' && !confirmedTools.length) {
      setLocalError(
        enabledTools.length
          ? 'Select at least one action requiring confirmation.'
          : 'Refresh the selected MCP servers before choosing individual confirmation rules.',
      )
      return
    }
    onCreate({
      name: draft.name.trim(),
      description: draft.description.trim(),
      system_prompt: draft.systemPrompt.trim(),
      model_id: draft.modelId,
      mcp_sources: sources.map(({ mcp_server_id, enabled_tools }) => ({
        mcp_server_id,
        enabled_tools,
      })),
      confirm_tools:
        !sources.length
          ? []
          : confirmMode === 'all'
            ? ['*']
            : confirmMode === 'selected'
              ? confirmedTools
              : [],
      confirm_tool_calls: Boolean(sources.length) && confirmMode !== 'none',
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

  const returnToLibrary = () => {
    setLibraryOpen(true)
    setChoosing(true)
    setCopyListOpen(false)
    setSource(undefined)
    setQuery('')
    setLocalError('')
  }

  const footer = choosing ? undefined : (
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
    <>
      <Modal
        open={open && resourceKind === null}
        side
        title="Add node"
        description={`Choose what to connect under ${parentName}.`}
        onClose={onClose}
      >
        <div className="node-kind-list">
          <button type="button" onClick={() => setResourceKind('subagent')}>
            <span className="node-kind-list__icon"><Bot size={18} /></span>
            <span><strong>Subagent</strong><small>Add a saved specialist or create a new one.</small></span>
            <ChevronRight size={16} />
          </button>
          <button type="button" onClick={() => setResourceKind('workflow')}>
            <span className="node-kind-list__icon node-kind-list__icon--workflow">
              <WorkflowIcon size={18} />
            </span>
            <span><strong>Workflow</strong><small>Reuse one of your saved workflows.</small></span>
            <ChevronRight size={16} />
          </button>
        </div>
      </Modal>
      <Modal
        open={open && resourceKind === 'workflow'}
        side
        title="Add workflow"
        description={`Select a workflow to add under ${parentName}.`}
        onClose={() => { setQuery(''); setResourceKind(null) }}
      >
        <div className="subagent-start subagent-library">
          <label className="subagent-start__search">
            <Search size={14} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search workflows…"
              autoFocus
            />
          </label>
          <div className="subagent-start__list">
            {visibleWorkflows.length ? visibleWorkflows.map((workflow) => (
              <button
                className="workflow-picker-row"
                type="button"
                key={workflow.id}
                disabled={busy}
                onClick={() => onConnectWorkflow?.(workflow.id)}
              >
                <span className="node-kind-list__icon node-kind-list__icon--workflow">
                  <WorkflowIcon size={17} />
                </span>
                <span>
                  <strong>{workflow.name}</strong>
                  <small>{workflow.description || `${workflow.execution_mode} workflow`}</small>
                </span>
              </button>
            )) : (
              <div className="empty-state subagent-start__empty">
                {query ? 'No matching workflows.' : 'No workflows available.'}
              </div>
            )}
          </div>
          <Feedback message={error} />
        </div>
      </Modal>
      <Modal
        open={open && resourceKind === 'subagent' && !previewAgent}
        wide
        side={libraryOpen}
        title={
          libraryOpen
            ? 'Add subagent'
            : choosing
              ? copyListOpen
                ? 'Use a template'
                : 'Create subagent'
              : source
                ? `Create from ${source.name}`
                : 'Create subagent'
        }
        description={
          libraryOpen
            ? `Select a subagent to add under ${parentName}, or create a new one.`
            : choosing
              ? copyListOpen
                ? 'Select a subagent to use as the starting point.'
                : 'Choose how you want to begin.'
              : source
                ? `Review and customize the new subagent before adding it under ${parentName}.`
                : `Configure the new subagent before adding it under ${parentName}.`
        }
        onClose={
          libraryOpen
            ? () => { setQuery(''); setResourceKind(null) }
            : returnToLibrary
        }
        footer={footer}
        className={!libraryOpen && choosing && !copyListOpen ? 'modal--subagent-choice' : undefined}
      >
        {libraryOpen ? (
          <div className="subagent-start subagent-library">
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
                  <AgentListItem
                    key={agent.id}
                    agent={agent}
                    disabledReason={unavailableReasons.get(agent.id)}
                    busy={busy}
                    onSelect={() => onConnect(agent.id)}
                    onDetails={() => setPreviewAgent(agent)}
                  />
                ))
              ) : (
                <div className="empty-state subagent-start__empty">
                  {query ? 'No matching subagents.' : 'No saved subagents yet.'}
                </div>
              )}
            </div>
            <Feedback message={error} />
            <div className="subagent-library__footer">
              <button
                className="subagent-library__create"
                type="button"
                onClick={() => {
                  setQuery('')
                  setLibraryOpen(false)
                  setChoosing(true)
                  setCopyListOpen(false)
                }}
              >
                <Plus size={13} />
                <span>Create subagent</span>
              </button>
            </div>
          </div>
        ) : choosing ? (
          <div className="subagent-start">
            {copyListOpen ? (
              <>
                <button
                  className="subagent-template__back"
                  type="button"
                  onClick={() => setCopyListOpen(false)}
                >
                  <ChevronLeft size={13} /> Back to creation options
                </button>
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
                      <AgentListItem
                        key={agent.id}
                        agent={agent}
                        onSelect={() => start(agent)}
                        onDetails={() => setPreviewAgent(agent)}
                      />
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
                    <strong>Create new configuration</strong>
                  </span>
                  <small>Configure a new subagent from scratch.</small>
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
                    <strong>Copy existing configuration</strong>
                  </span>
                  <small>Start with a copy of a saved subagent configuration.</small>
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
                {!servers.length && (
                  <div className="guidance">
                    No MCP servers are connected. You can still create a prompt-only subagent.
                  </div>
                )}
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
                        onClick={() =>
                          setDraft({ ...draft, iconData: '', sourceIconId: undefined })
                        }
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
                    onChange={(event) =>
                      setDraft({ ...draft, modelId: Number(event.target.value) })
                    }
                  >
                    <option value="">Choose a model…</option>
                    {models.map((model) => (
                      <option key={model.id} value={model.id}>
                        {model.name} — {model.provider}
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
                    <strong>Capabilities</strong>
                    <small>
                      Select individual tools across any connected MCP servers, or keep this subagent prompt-only.
                    </small>
                  </span>
                </div>
                <MultiServerToolPicker
                  servers={servers}
                  groups={toolGroups}
                  sources={sources}
                  onChange={setSources}
                />
              </div>
            )}

            {step === 3 && (
              <div className="form-grid subagent-wizard__page">
                {!sources.length && (
                  <div className="guidance">
                    Prompt-only subagents have no external actions to approve. You can create this subagent as-is.
                  </div>
                )}
                <Field
                  full
                  label="Action confirmation"
                  hint="Control which actions require your approval before they run."
                >
                  <select
                    value={confirmMode}
                    disabled={!sources.length}
                    onChange={(event) => setConfirmMode(event.target.value as ConfirmationMode)}
                  >
                    <option value="all">Ask before every action</option>
                    <option value="selected">Ask for selected actions</option>
                    <option value="none">Run without confirmation</option>
                  </select>
                </Field>
                {sources.length > 0 && confirmMode === 'selected' && (
                  <div className="key-value-editor">
                    <div className="key-value-editor__title">
                      <span>
                        <strong>Actions requiring confirmation</strong>
                        <small>Select the actions Mounir must ask you to approve.</small>
                      </span>
                    </div>
                    <ToolChoices
                      tools={enabledTools}
                      selected={confirmedSelection}
                      onChange={setConfirmed}
                      empty="Enable at least one tool before configuring confirmation rules."
                    />
                  </div>
                )}
                {sources.length > 0 && <div className="key-value-editor">
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
                    selected={dedupedSelection}
                    onChange={setDeduped}
                    empty="Enable at least one tool before configuring repeated-action protection."
                  />
                </div>}
              </div>
            )}

            <Feedback
              message={
                localError ||
                error ||
                ''
              }
            />
          </form>
        )}
      </Modal>
      <Modal
        open={Boolean(previewAgent)}
        wide
        title={previewAgent?.name || 'Subagent details'}
        description="Read-only subagent configuration."
        onClose={() => setPreviewAgent(null)}
      >
        {previewAgent && <SubagentPreview agent={previewAgent} />}
      </Modal>
    </>
  )
}
