import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import { Check, ChevronLeft, ChevronRight, Copy, Eye, Plus, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
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
import { useAgentNodes, useSkills, useWorkflowNodes } from '../../hooks/useStudioData'
import { readable, stringList, toDataUrl } from '../resources/helpers'
import {
  expandLegacyToolRules,
  MultiServerToolPicker,
  selectedToolOptions,
  useMcpToolCatalog,
} from '../resources/McpToolAccess'
import { ToolChoices } from '../resources/ToolChoices'
import { SkillPicker } from '../resources/SkillPicker'
import { agentNodeTypes, type FlowData } from './AgentFlowNode'

type WizardStep = 1 | 2 | 3 | 4
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

function WorkflowListItem({
  workflow,
  busy,
  onSelect,
  onDetails,
}: {
  workflow: Workflow
  busy?: boolean
  onSelect: () => void
  onDetails: () => void
}) {
  return (
    <div className="subagent-start__agent subagent-start__agent--workflow">
      <button
        className="subagent-start__agent-select"
        type="button"
        disabled={busy}
        onClick={onSelect}
      >
        <span>
          <strong>{workflow.name}</strong>
          <small>{workflow.description || `${workflow.execution_mode} workflow`}</small>
        </span>
      </button>
      <button
        className="subagent-start__agent-details"
        type="button"
        onClick={onDetails}
        aria-label={`View ${workflow.name} details`}
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
              sources.map(
                (source) =>
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

function previewEdge(source: string, target: string): Edge {
  return {
    id: `${source}-${target}`,
    source,
    target,
    animated: true,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#59c98e', width: 13, height: 13 },
    style: { stroke: '#59c98e', strokeWidth: 1.6, strokeDasharray: '6 7' },
  }
}

function WorkflowPreviewOverview({ workflow }: { workflow: Workflow }) {
  const agentNodes = useAgentNodes(workflow.id)
  const workflowNodes = useWorkflowNodes(workflow.id)
  const graph = useMemo(() => {
    const subagents = agentNodes.data || []
    const nested = workflowNodes.data || []
    const nodes: Node<FlowData>[] = []
    const edges: Edge[] = []

    if (workflow.execution_mode === 'direct') {
      const row = [
        ...subagents.map((item) => ({ kind: 'subagent' as const, item })),
        ...nested.map((item) => ({ kind: 'workflow' as const, item })),
      ].sort((a, b) => a.item.position - b.item.position || a.item.id - b.item.id)
      nodes.push({
        id: 'start',
        type: 'agent',
        position: { x: 20, y: 150 },
        data: { label: 'Start', kind: 'workflow-start', flowDirection: 'horizontal' },
      })
      let previous = 'start'
      row.forEach(({ kind, item }, index) => {
        const id = `${kind}-${item.id}`
        const isSubagent = kind === 'subagent'
        nodes.push({
          id,
          type: 'agent',
          position: { x: 240 + index * 230, y: 150 },
          data: {
            label: item.name,
            kind: isSubagent ? 'dynamic' : 'workflow',
            flowDirection: 'horizontal',
            ...(isSubagent
              ? { model: item.model || item.model_name }
              : { workflowMode: item.execution_mode }),
            icon:
              isSubagent && item.has_icon ? `/api/subagents/${item.subagent_id}/icon` : undefined,
          },
        })
        edges.push(previewEdge(previous, id))
        previous = id
      })
      nodes.push({
        id: 'end',
        type: 'agent',
        position: { x: row.length ? 250 + row.length * 230 : 350, y: 150 },
        data: { label: 'End', kind: 'workflow-end', flowDirection: 'horizontal' },
      })
      edges.push(previewEdge(previous, 'end'))
      return { nodes, edges }
    }

    nodes.push({
      id: 'orchestrator',
      type: 'agent',
      position: { x: 350, y: 30 },
      data: {
        label: 'Orchestrator',
        kind: 'orchestrator',
        model: workflow.model || workflow.model_name || 'No model selected',
      },
    })
    const byDepth = new Map<number, number>()
    subagents.forEach((item) => {
      const depth = Math.max(0, (item.depth || 1) - 1)
      const index = byDepth.get(depth) || 0
      byDepth.set(depth, index + 1)
      const id = `subagent-${item.id}`
      const parentId =
        item.parent_node_id === null ? 'orchestrator' : `subagent-${item.parent_node_id}`
      nodes.push({
        id,
        type: 'agent',
        position: { x: 80 + index * 225, y: 205 + depth * 135 },
        data: {
          label: item.name,
          kind: 'dynamic',
          enabled: item.enabled,
          model: item.model || item.model_name,
          icon: item.has_icon ? `/api/subagents/${item.subagent_id}/icon` : undefined,
        },
      })
      edges.push(previewEdge(parentId, id))
    })
    nested.forEach((item, index) => {
      const id = `workflow-${item.id}`
      const parentId =
        item.parent_node_id === null ? 'orchestrator' : `subagent-${item.parent_node_id}`
      nodes.push({
        id,
        type: 'agent',
        position: { x: 80 + (subagents.length + index) * 225, y: 205 },
        data: {
          label: item.name,
          kind: 'workflow',
          workflowMode: item.execution_mode,
        },
      })
      edges.push(previewEdge(parentId, id))
    })
    return { nodes, edges }
  }, [agentNodes.data, workflow, workflowNodes.data])

  if (agentNodes.isLoading || workflowNodes.isLoading) {
    return <div className="workflow-preview__loading">Loading workflow…</div>
  }
  const loadError = agentNodes.error || workflowNodes.error
  if (loadError) {
    return (
      <Feedback
        message={loadError instanceof Error ? loadError.message : 'Could not load the workflow.'}
      />
    )
  }
  return (
    <div className="workflow-preview__canvas">
      <ReactFlow
        nodes={graph.nodes}
        edges={graph.edges}
        nodeTypes={agentNodeTypes}
        fitView
        minZoom={0.3}
        maxZoom={1.7}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        proOptions={{ hideAttribution: true }}
        onInit={(instance) => {
          requestAnimationFrame(() => instance.fitView({ padding: 0.2 }))
        }}
      >
        <Background variant={BackgroundVariant.Dots} color="#587064" gap={18} size={1.3} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}

function WorkflowPreview({ workflow }: { workflow: Workflow }) {
  const [tab, setTab] = useState<'overview' | 'details'>('overview')
  useEffect(() => setTab('overview'), [workflow.id])
  return (
    <div className="workflow-preview">
      <nav className="workflow-tabs workflow-preview__tabs" aria-label="Workflow preview pages">
        <button
          type="button"
          className={tab === 'overview' ? 'is-active' : ''}
          onClick={() => setTab('overview')}
        >
          Overview
        </button>
        <button
          type="button"
          className={tab === 'details' ? 'is-active' : ''}
          onClick={() => setTab('details')}
        >
          Details
        </button>
      </nav>
      {tab === 'overview' ? (
        <WorkflowPreviewOverview workflow={workflow} />
      ) : (
        <dl className="detail-grid workflow-preview__details">
          <PreviewValue label="Name" value={workflow.name} />
          <PreviewValue
            label="Execution mode"
            value={workflow.execution_mode === 'agentic' ? 'Agentic' : 'Direct'}
          />
          <PreviewValue label="Description" value={workflow.description || 'No description'} full />
          {workflow.execution_mode === 'agentic' && (
            <>
              <PreviewValue
                label="Orchestrator model"
                value={
                  workflow.model_name
                    ? `${workflow.model_name}${workflow.model ? ` — ${workflow.model}` : ''}`
                    : workflow.model || 'Not configured'
                }
                full
              />
              <PreviewValue
                label="Orchestrator system prompt"
                value={workflow.system_prompt || 'Default instructions'}
                full
              />
            </>
          )}
        </dl>
      )}
    </div>
  )
}

export function WorkflowPreviewModal({
  workflow,
  onClose,
}: {
  workflow: Workflow | null
  onClose: () => void
}) {
  const navigate = useNavigate()
  return (
    <Modal
      open={Boolean(workflow)}
      wide
      className="modal--workflow-preview"
      title={workflow?.name || 'Workflow details'}
      description="Read-only workflow configuration."
      onClose={onClose}
      footer={
        workflow ? (
          <Button
            type="button"
            variant="primary"
            onClick={() => {
              onClose()
              navigate(`/admin/workflows?open=${workflow.id}&tab=overview`)
            }}
          >
            View workflow <ChevronRight size={13} />
          </Button>
        ) : undefined
      }
    >
      {workflow && <WorkflowPreview workflow={workflow} />}
    </Modal>
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
  const skills = useSkills()
  const [selectedSkills, setSelectedSkills] = useState(new Set<number>())
  const [confirmMode, setConfirmMode] = useState<ConfirmationMode>('all')
  const [confirmed, setConfirmed] = useState(new Set<string>())
  const [deduped, setDeduped] = useState(new Set<string>())
  const [localError, setLocalError] = useState('')
  const [copyingIcon, setCopyingIcon] = useState(false)
  const [previewAgent, setPreviewAgent] = useState<Subagent | null>(null)
  const [previewWorkflow, setPreviewWorkflow] = useState<Workflow | null>(null)

  const toolGroups = useMcpToolCatalog(servers)
  const enabledTools = useMemo(
    () => selectedToolOptions(toolGroups, sources),
    [toolGroups, sources],
  )
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
    setSelectedSkills(new Set())
    setConfirmMode('all')
    setConfirmed(new Set())
    setDeduped(new Set())
    setLocalError('')
    setCopyingIcon(false)
    setPreviewAgent(null)
    setPreviewWorkflow(null)
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
    setSelectedSkills(new Set((agent?.skill_ids || []).map(Number)))
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
      skill_ids: [...selectedSkills].sort((left, right) => left - right),
      confirm_tools: !sources.length
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
      {step < 4 ? (
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
            <span className="node-kind-list__copy">
              <strong>Subagent</strong>
              <small>Add a saved specialist or create a new one.</small>
            </span>
            <ChevronRight size={16} />
          </button>
          <button type="button" onClick={() => setResourceKind('workflow')}>
            <span className="node-kind-list__copy">
              <strong>Workflow</strong>
              <small>Reuse one of your saved workflows.</small>
            </span>
            <ChevronRight size={16} />
          </button>
        </div>
      </Modal>
      <Modal
        open={open && resourceKind === 'workflow' && !previewWorkflow}
        side
        title="Add workflow"
        description={`Select a workflow to add under ${parentName}.`}
        onClose={() => {
          setQuery('')
          setResourceKind(null)
        }}
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
            {visibleWorkflows.length ? (
              visibleWorkflows.map((workflow) => (
                <WorkflowListItem
                  key={workflow.id}
                  workflow={workflow}
                  busy={busy}
                  onSelect={() => onConnectWorkflow?.(workflow.id)}
                  onDetails={() => setPreviewWorkflow(workflow)}
                />
              ))
            ) : (
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
            ? () => {
                setQuery('')
                setResourceKind(null)
              }
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
                { number: 2, label: 'Skills' },
                { number: 3, label: 'Tools' },
                { number: 4, label: 'Security' },
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
                <p className="subagent-wizard__section-description">
                  Select the installed skills this subagent can discover and activate.
                </p>
                <SkillPicker
                  skills={skills.data || []}
                  selected={selectedSkills}
                  loading={skills.isLoading}
                  error={skills.error instanceof Error ? skills.error.message : ''}
                  onChange={setSelectedSkills}
                />
              </div>
            )}

            {step === 3 && (
              <div className="subagent-wizard__page">
                <p className="subagent-wizard__section-description">
                  Choose tools from available MCP servers to connect them to your subagent.
                </p>
                <MultiServerToolPicker
                  servers={servers}
                  groups={toolGroups}
                  sources={sources}
                  onChange={setSources}
                />
              </div>
            )}

            {step === 4 && (
              <div className="form-grid subagent-wizard__page">
                {!sources.length && (
                  <div className="guidance">
                    Prompt-only subagents have no external actions to approve. You can create this
                    subagent as-is.
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
                {sources.length > 0 && (
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
                      selected={dedupedSelection}
                      onChange={setDeduped}
                      empty="Enable at least one tool before configuring repeated-action protection."
                    />
                  </div>
                )}
              </div>
            )}

            <Feedback message={localError || error || ''} />
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
      <WorkflowPreviewModal workflow={previewWorkflow} onClose={() => setPreviewWorkflow(null)} />
    </>
  )
}
