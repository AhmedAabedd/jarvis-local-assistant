import {
  BaseEdge,
  Background,
  BackgroundVariant,
  Controls,
  EdgeLabelRenderer,
  getBezierPath,
  MarkerType,
  ReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
} from '@xyflow/react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ChevronLeft,
  GitBranch,
  LayoutDashboard,
  ListChecks,
  Plus,
  Save,
  Search,
  Settings2,
  Trash2,
  Workflow as WorkflowIcon,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import type { ModelRecord, Workflow } from '../../api/types'
import { AutoTextarea } from '../../components/ui/AutoTextarea'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { Modal } from '../../components/ui/Modal'
import { SectionTabs } from '../../components/ui/SectionTabs'
import {
  keys,
  useAgentNodes,
  useAgents,
  useModels,
  useServers,
  useWorkflowNodes,
  useWorkflows,
} from '../../hooks/useStudioData'
import { agentNodeTypes, createLayeredFlowLayout, type FlowData } from '../overview/AgentFlowNode'
import { ConnectAgentModal, WorkflowPreviewModal } from '../overview/ConnectAgentModal'
import { PageHeader } from '../studio/PageHeader'

type Tab = 'overview' | 'details'
type Parent = { nodeId: number | null; name: string; position?: number }
type PendingNode = { kind: 'subagent' | 'workflow'; id: number; name: string }

function WorkflowForm({
  workflow,
  models,
  busy,
  error,
  onSubmit,
}: {
  workflow?: Workflow
  models: ModelRecord[]
  busy: boolean
  error: string
  onSubmit: (body: object) => void
}) {
  const [name, setName] = useState(workflow?.name || '')
  const [description, setDescription] = useState(workflow?.description || '')
  const [modelId, setModelId] = useState(workflow?.model_id || 0)
  const [prompt, setPrompt] = useState(workflow?.system_prompt || '')
  const [mode, setMode] = useState<'agentic' | 'direct'>(workflow?.execution_mode || 'agentic')

  return (
    <form
      id="workflow-form"
      className="workflow-form"
      aria-busy={busy}
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit({
          name: name.trim(),
          description: description.trim(),
          execution_mode: mode,
          model_id: mode === 'agentic' ? modelId || null : null,
          system_prompt: mode === 'agentic' ? prompt.trim() : '',
        })
      }}
    >
      <div className="form-grid">
        <Field label="Name" full>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            autoFocus={!workflow}
          />
        </Field>
        <Field label="Description" full>
          <AutoTextarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="What this workflow is designed to accomplish"
          />
        </Field>
        <Field label="Execution mode" full>
          <div className="workflow-mode-options">
            <button
              type="button"
              className={mode === 'agentic' ? 'is-selected' : ''}
              onClick={() => setMode('agentic')}
            >
              <GitBranch size={18} />
              <span>
                <strong>Agentic</strong>
                <small>Branching workflow design</small>
              </span>
            </button>
            <button
              type="button"
              className={mode === 'direct' ? 'is-selected' : ''}
              onClick={() => setMode('direct')}
            >
              <ListChecks size={18} />
              <span>
                <strong>Direct</strong>
                <small>Sequential workflow design</small>
              </span>
            </button>
          </div>
        </Field>
        {mode === 'agentic' && (
          <>
            <Field
              label="Orchestrator model"
              hint="Any saved OpenAI-compatible model can orchestrate this workflow."
              full
            >
              <select
                value={modelId || ''}
                onChange={(event) => setModelId(Number(event.target.value))}
              >
                <option value="">Choose a model…</option>
                {models.map((model) => (
                  <option value={model.id} key={model.id}>
                    {model.name} — {model.model}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="Orchestrator system prompt"
              hint="Explain how the orchestrator should delegate to the connected nodes."
              full
            >
              <AutoTextarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                rows={8}
              />
            </Field>
          </>
        )}
      </div>
      <Feedback message={error} />
    </form>
  )
}

function AddNodeEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  data,
}: EdgeProps) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  })
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{ stroke: '#59c98e', strokeWidth: 1.8, strokeDasharray: '6 7' }}
      />
      <EdgeLabelRenderer>
        <button
          className="workflow-edge-add nodrag nopan"
          type="button"
          style={{
            pointerEvents: 'all',
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          }}
          onClick={() => (data as { onAdd?: () => void } | undefined)?.onAdd?.()}
          aria-label="Create the first node"
          title="Create the first node"
        >
          <Plus size={14} />
        </button>
      </EdgeLabelRenderer>
    </>
  )
}

const workflowEdgeTypes = { addNode: AddNodeEdge }

function OrchestratorWizard({
  open,
  workflow,
  models,
  busy,
  error,
  onSave,
  onClose,
}: {
  open: boolean
  workflow: Workflow
  models: ModelRecord[]
  busy: boolean
  error: string
  onSave: (modelId: number, prompt: string) => void
  onClose: () => void
}) {
  const [modelId, setModelId] = useState(workflow.model_id || 0)
  const [prompt, setPrompt] = useState(workflow.system_prompt || '')

  useEffect(() => {
    if (!open) return
    setModelId(workflow.model_id || 0)
    setPrompt(workflow.system_prompt || '')
  }, [open, workflow.id, workflow.model_id, workflow.system_prompt])

  return (
    <Modal
      open={open}
      wide
      title="Configure orchestrator"
      description="Choose the model and instructions used to delegate work inside this workflow."
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon={<Save size={14} />}
            busy={busy}
            disabled={!modelId}
            onClick={() => onSave(modelId, prompt)}
          >
            Save orchestrator
          </Button>
        </>
      }
    >
      <div className="form-grid orchestrator-wizard">
        <Field
          label="Model"
          hint="Any saved OpenAI-compatible model can orchestrate this workflow."
          full
        >
          <select
            value={modelId || ''}
            onChange={(event) => setModelId(Number(event.target.value))}
            autoFocus
          >
            <option value="">Choose a model…</option>
            {models.map((model) => (
              <option value={model.id} key={model.id}>
                {model.name} — {model.model}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="System prompt"
          hint="Explain how the orchestrator should delegate to the connected nodes."
          full
        >
          <AutoTextarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            rows={8}
          />
        </Field>
      </div>
      <Feedback
        message={
          error || (!models.length ? 'Create a model before configuring the orchestrator.' : '')
        }
      />
    </Modal>
  )
}

function WorkflowOverview({ workflow, workflows }: { workflow: Workflow; workflows: Workflow[] }) {
  const client = useQueryClient()
  const navigate = useNavigate()
  const agents = useAgents()
  const models = useModels()
  const servers = useServers()
  const agentNodes = useAgentNodes(workflow.id)
  const workflowNodes = useWorkflowNodes(workflow.id)
  const [parent, setParent] = useState<Parent | null>(null)
  const [pending, setPending] = useState<PendingNode | null>(null)
  const [orchestratorOpen, setOrchestratorOpen] = useState(false)
  const [previewWorkflow, setPreviewWorkflow] = useState<Workflow | null>(null)

  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: keys.agents }),
      client.invalidateQueries({ queryKey: keys.agentNodes }),
      client.invalidateQueries({ queryKey: keys.skills }),
      client.invalidateQueries({ queryKey: keys.workflowNodes }),
      client.invalidateQueries({ queryKey: keys.workflows }),
    ])
  const createAgent = useMutation({
    mutationFn: (body: object) =>
      api.agents.create({
        ...body,
        workflow_id: workflow.id,
        parent_node_id: workflow.execution_mode === 'direct' ? null : (parent?.nodeId ?? null),
        ...(workflow.execution_mode === 'direct' && parent?.position !== undefined
          ? { position: parent.position }
          : {}),
      }),
    onSuccess: async () => {
      await refresh()
      setParent(null)
    },
  })
  const connectAgent = useMutation({
    mutationFn: (subagentId: number) =>
      api.agentNodes.create({
        subagent_id: subagentId,
        workflow_id: workflow.id,
        parent_node_id: workflow.execution_mode === 'direct' ? null : (parent?.nodeId ?? null),
        ...(workflow.execution_mode === 'direct' && parent?.position !== undefined
          ? { position: parent.position }
          : {}),
      }),
    onSuccess: async () => {
      await refresh()
      setParent(null)
    },
  })
  const connectWorkflow = useMutation({
    mutationFn: (childWorkflowId: number) =>
      api.workflowNodes.create({
        child_workflow_id: childWorkflowId,
        owner_workflow_id: workflow.id,
        parent_node_id: workflow.execution_mode === 'direct' ? null : (parent?.nodeId ?? null),
        ...(workflow.execution_mode === 'direct' && parent?.position !== undefined
          ? { position: parent.position }
          : {}),
      }),
    onSuccess: async () => {
      await refresh()
      setParent(null)
    },
  })
  const removeNode = useMutation({
    mutationFn: (item: PendingNode) =>
      item.kind === 'subagent' ? api.agentNodes.remove(item.id) : api.workflowNodes.remove(item.id),
    onSuccess: async () => {
      await refresh()
      setPending(null)
    },
  })
  const saveOrchestrator = useMutation({
    mutationFn: ({ modelId, prompt }: { modelId: number; prompt: string }) =>
      api.workflows.update(workflow.id, {
        model_id: modelId,
        system_prompt: prompt.trim(),
      }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: keys.workflows })
      setOrchestratorOpen(false)
    },
  })

  useEffect(() => {
    if (parent) {
      createAgent.reset()
      connectAgent.reset()
      connectWorkflow.reset()
    }
  }, [parent])

  const graph = useMemo(() => {
    const subagents = agentNodes.data || []
    const nested = workflowNodes.data || []
    const nodes: Node<FlowData>[] = []
    const edges: Edge[] = []
    const direct = workflow.execution_mode === 'direct'
    const row = [
      ...subagents.map((item) => ({ kind: 'subagent' as const, item })),
      ...nested.map((item) => ({ kind: 'workflow' as const, item })),
    ].sort((a, b) => a.item.position - b.item.position || a.item.id - b.item.id)

    if (direct) {
      nodes.push({
        id: 'start',
        type: 'agent',
        position: { x: 20, y: 190 },
        data: {
          label: 'Start',
          kind: 'workflow-start',
          flowDirection: 'horizontal',
        },
      })
      let previous = 'start'
      row.forEach(({ kind, item }, index) => {
        const id = `${kind}-${item.id}`
        const isSubagent = kind === 'subagent'
        nodes.push({
          id,
          type: 'agent',
          position: { x: 250 + index * 230, y: 190 },
          data: {
            label: item.name,
            kind: isSubagent ? 'dynamic' : 'workflow',
            flowDirection: 'horizontal',
            ...(isSubagent
              ? { model: item.model || item.model_name }
              : { workflowMode: item.execution_mode }),
            icon:
              isSubagent && item.has_icon ? `/api/subagents/${item.subagent_id}/icon` : undefined,
            onOpen: () =>
              isSubagent
                ? navigate(`/admin/agents?open=${item.subagent_id}`)
                : setPreviewWorkflow(
                    workflows.find((candidate) => candidate.id === item.child_workflow_id) || null,
                  ),
            onDelete: () => setPending({ kind, id: item.id, name: item.name }),
          },
        })
        edges.push({
          id: `${previous}-${id}-add`,
          source: previous,
          target: id,
          type: 'addNode',
          markerEnd: { type: MarkerType.ArrowClosed, color: '#59c98e', width: 13, height: 13 },
          data: {
            onAdd: () => setParent({ nodeId: null, name: workflow.name, position: index }),
          },
        })
        previous = id
      })
      const endX = row.length ? 260 + row.length * 230 : 430
      nodes.push({
        id: 'end',
        type: 'agent',
        position: { x: endX, y: 190 },
        data: { label: 'End', kind: 'workflow-end', flowDirection: 'horizontal' },
      })
      edges.push({
        id: `${previous}-end-add`,
        source: previous,
        target: 'end',
        type: 'addNode',
        markerEnd: { type: MarkerType.ArrowClosed, color: '#59c98e', width: 13, height: 13 },
        data: {
          onAdd: () => setParent({ nodeId: null, name: workflow.name, position: row.length }),
        },
      })
      return { nodes, edges }
    }

    const occurrences = subagents
      .map((item) => ({
        item,
        depth: Math.max(0, (item.depth || 1) - 1),
      }))
      .sort(
        (left, right) => left.depth - right.depth || left.item.name.localeCompare(right.item.name),
      )
    const workflowDepths = nested.map((item) => {
      const parentNode = subagents.find((node) => node.node_id === item.parent_node_id)
      return parentNode ? Math.max(0, parentNode.depth || 1) : 0
    })
    const { center, nextPosition } = createLayeredFlowLayout([
      ...occurrences.map(({ depth }) => depth),
      ...workflowDepths,
    ])

    nodes.push({
      id: 'orchestrator',
      type: 'agent',
      position: { x: center - 105, y: 145 },
      data: {
        label: 'Orchestrator',
        kind: 'orchestrator',
        model: workflow.model || workflow.model_name || 'Select a model',
        onOpen: () => setOrchestratorOpen(true),
        onAdd: () => setParent({ nodeId: null, name: workflow.name }),
      },
    })
    occurrences.forEach(({ item, depth }) => {
      const id = `subagent-${item.id}`
      const parentId =
        item.parent_node_id === null ? 'orchestrator' : `subagent-${item.parent_node_id}`
      nodes.push({
        id,
        type: 'agent',
        position: nextPosition(depth),
        data: {
          label: item.name,
          kind: 'dynamic',
          enabled: item.enabled,
          model: item.model || item.model_name,
          icon: item.has_icon ? `/api/subagents/${item.subagent_id}/icon` : undefined,
          onOpen: () => navigate(`/admin/agents?open=${item.subagent_id}`),
          onAdd:
            item.depth < 4
              ? () => setParent({ nodeId: item.id, name: item.path_label })
              : undefined,
          onDelete: () => setPending({ kind: 'subagent', id: item.id, name: item.name }),
        },
      })
      edges.push({
        id: `${parentId}-${id}`,
        source: parentId,
        target: id,
        animated: Boolean(item.enabled),
        style: { stroke: item.enabled ? '#79b8ff' : '#ff7d79' },
      })
    })
    nested.forEach((item, index) => {
      const depth = workflowDepths[index]
      const id = `workflow-${item.id}`
      const parentId =
        item.parent_node_id === null ? 'orchestrator' : `subagent-${item.parent_node_id}`
      nodes.push({
        id,
        type: 'agent',
        position: nextPosition(depth),
        data: {
          label: item.name,
          kind: 'workflow',
          workflowMode: item.execution_mode,
          onOpen: () =>
            setPreviewWorkflow(
              workflows.find((candidate) => candidate.id === item.child_workflow_id) || null,
            ),
          onDelete: () => setPending({ kind: 'workflow', id: item.id, name: item.name }),
        },
      })
      edges.push({
        id: `${parentId}-${id}`,
        source: parentId,
        target: id,
        animated: true,
        style: { stroke: '#f472b6' },
      })
    })
    return { nodes, edges }
  }, [agentNodes.data, navigate, workflow, workflowNodes.data, workflows])

  if (agentNodes.isLoading || workflowNodes.isLoading) return <Loading />
  const modalError = [createAgent.error, connectAgent.error, connectWorkflow.error].find(
    (value) => value instanceof Error,
  )
  return (
    <>
      <section className="card flow-card workflow-flow-card">
        <ReactFlow
          nodes={graph.nodes}
          edges={graph.edges}
          nodeTypes={agentNodeTypes}
          edgeTypes={workflowEdgeTypes}
          fitView
          minZoom={0.3}
          maxZoom={1.7}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} color="#587064" gap={18} size={1.3} />
          <Controls />
        </ReactFlow>
      </section>
      {workflow.execution_mode === 'agentic' && (
        <OrchestratorWizard
          open={orchestratorOpen}
          workflow={workflow}
          models={models.data || []}
          busy={saveOrchestrator.isPending}
          error={saveOrchestrator.error instanceof Error ? saveOrchestrator.error.message : ''}
          onSave={(modelId, prompt) => saveOrchestrator.mutate({ modelId, prompt })}
          onClose={() => {
            if (!saveOrchestrator.isPending) setOrchestratorOpen(false)
          }}
        />
      )}
      <ConnectAgentModal
        open={Boolean(parent)}
        parentNodeId={parent?.nodeId ?? null}
        parentName={parent?.name || workflow.name}
        models={models.data || []}
        servers={servers.data || []}
        agents={agents.data || []}
        placements={agentNodes.data || []}
        workflows={workflows}
        currentWorkflowId={workflow.id}
        busy={createAgent.isPending || connectAgent.isPending || connectWorkflow.isPending}
        error={modalError instanceof Error ? modalError.message : ''}
        onCreate={(body) => createAgent.mutate(body)}
        onConnect={(id) => connectAgent.mutate(id)}
        onConnectWorkflow={(id) => connectWorkflow.mutate(id)}
        onClose={() => setParent(null)}
      />
      <WorkflowPreviewModal workflow={previewWorkflow} onClose={() => setPreviewWorkflow(null)} />
      <ConfirmDialog
        open={Boolean(pending)}
        title="Disconnect node?"
        message={`Disconnect “${pending?.name || ''}” from this workflow? Its saved configuration will remain available.`}
        confirmLabel="Disconnect"
        danger
        busy={removeNode.isPending}
        error={removeNode.error instanceof Error ? removeNode.error.message : ''}
        onConfirm={() => pending && removeNode.mutate(pending)}
        onCancel={() => setPending(null)}
      />
    </>
  )
}

export function WorkflowsPage() {
  const client = useQueryClient()
  const workflows = useWorkflows()
  const models = useModels()
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState('')
  const [pendingDelete, setPendingDelete] = useState<Workflow | null>(null)
  const [workflowFormDirty, setWorkflowFormDirty] = useState(false)
  const [workflowDraftVersion, setWorkflowDraftVersion] = useState(0)
  const openId = Number(params.get('open') || 0)
  const creating = params.get('new') === '1'
  const tab: Tab = params.get('tab') === 'details' ? 'details' : 'overview'
  const selected = workflows.data?.find((item) => item.id === openId)

  const create = useMutation({
    mutationFn: (body: object) => api.workflows.create(body),
    onSuccess: async (workflow) => {
      await client.invalidateQueries({ queryKey: keys.workflows })
      setWorkflowFormDirty(false)
      setParams({ open: String(workflow.id), tab: 'overview' })
    },
  })
  const update = useMutation({
    mutationFn: (body: object) => api.workflows.update(openId, body),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: keys.workflows })
      setWorkflowFormDirty(false)
    },
  })
  const remove = useMutation({
    mutationFn: (id: number) => api.workflows.remove(id),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: keys.workflows })
      setPendingDelete(null)
      setParams({})
    },
  })

  const visible = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    return (workflows.data || []).filter(
      (item) =>
        !normalized ||
        item.name.toLocaleLowerCase().includes(normalized) ||
        item.description.toLocaleLowerCase().includes(normalized),
    )
  }, [query, workflows.data])

  if (workflows.isLoading || models.isLoading) {
    return (
      <>
        <PageHeader title="Workflows" description="Design reusable agent workflows" />
        <Loading />
      </>
    )
  }
  if (creating) {
    return (
      <>
        <PageHeader
          title="Create workflow"
          description="Save a reusable agentic or direct workflow"
          leading={
            <Button
              icon={<ChevronLeft size={14} />}
              onClick={() => {
                setWorkflowFormDirty(false)
                setParams({})
              }}
              aria-label="Back to workflows"
              title="Back to workflows"
            />
          }
          actions={
            <Button
              form="workflow-form"
              type="submit"
              variant="primary"
              icon={<Save size={14} />}
              busy={create.isPending}
            >
              Create
            </Button>
          }
        />
        <div className="page-content">
          <section className="card resource-workspace resource-workspace--form">
            <div className="card__body">
              <WorkflowForm
                models={models.data || []}
                busy={create.isPending}
                error={create.error instanceof Error ? create.error.message : ''}
                onSubmit={(body) => create.mutate(body)}
              />
            </div>
          </section>
        </div>
      </>
    )
  }
  if (selected) {
    return (
      <>
        <PageHeader
          title={selected.name}
          description={selected.description || 'Reusable workflow'}
          leading={
            <Button
              icon={<ChevronLeft size={14} />}
              onClick={() => setParams({})}
              aria-label="Back to workflows"
              title="Back to workflows"
            />
          }
          actions={
            <>
              {tab === 'details' && workflowFormDirty && (
                <>
                  <Button
                    className="resource-header-action"
                    icon={<X size={15} />}
                    onClick={() => {
                      update.reset()
                      setWorkflowFormDirty(false)
                      setWorkflowDraftVersion((version) => version + 1)
                    }}
                    aria-label="Discard changes"
                    title="Discard changes"
                  />
                  <Button
                    form="workflow-form"
                    type="submit"
                    variant="primary"
                    icon={<Save size={14} />}
                    busy={update.isPending}
                  >
                    Save
                  </Button>
                </>
              )}
              <Button
                className="resource-header-action"
                icon={<Trash2 size={15} />}
                onClick={() => setPendingDelete(selected)}
                aria-label="Delete workflow"
                title="Delete workflow"
              />
            </>
          }
        />
        <SectionTabs
          className="workflow-tabs"
          label="Workflow pages"
          value={tab}
          options={[
            { id: 'overview', label: 'Overview', icon: <LayoutDashboard size={14} /> },
            { id: 'details', label: 'Details', icon: <Settings2 size={14} /> },
          ]}
          onChange={(value) => {
            setWorkflowFormDirty(false)
            setParams({ open: String(selected.id), tab: value })
          }}
        />
        <div className="page-content">
          {tab === 'overview' ? (
            <WorkflowOverview workflow={selected} workflows={workflows.data || []} />
          ) : (
            <section
              key={`workflow:${selected.id}:${workflowDraftVersion}`}
              className="card resource-workspace resource-workspace--form"
              onChangeCapture={() => setWorkflowFormDirty(true)}
              onInputCapture={() => setWorkflowFormDirty(true)}
              onClickCapture={(event) => {
                if ((event.target as HTMLElement).closest('button[type="button"]'))
                  setWorkflowFormDirty(true)
              }}
            >
              <div className="card__body">
                <WorkflowForm
                  key={selected.id}
                  workflow={selected}
                  models={models.data || []}
                  busy={update.isPending}
                  error={update.error instanceof Error ? update.error.message : ''}
                  onSubmit={(body) => update.mutate(body)}
                />
              </div>
            </section>
          )}
        </div>
        <ConfirmDialog
          open={Boolean(pendingDelete)}
          title="Delete workflow?"
          message={`Permanently delete “${pendingDelete?.name || ''}”? Workflows that reuse it must be disconnected first.`}
          confirmLabel="Delete"
          danger
          busy={remove.isPending}
          error={remove.error instanceof Error ? remove.error.message : ''}
          onConfirm={() => pendingDelete && remove.mutate(pendingDelete.id)}
          onCancel={() => setPendingDelete(null)}
        />
      </>
    )
  }
  return (
    <>
      <PageHeader
        title="Workflows"
        description="Design and reuse agentic or direct workflows"
        actions={
          <Button
            variant="primary"
            icon={<Plus size={14} />}
            onClick={() => setParams({ new: '1' })}
          >
            New workflow
          </Button>
        }
      />
      <div className="page-content">
        <div className="resource-browser">
          <label className="resource-search">
            <Search size={14} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search workflows…"
            />
          </label>
          <div className="resource-list">
            {visible.map((workflow) => (
              <button
                className="resource-row workflow-card"
                key={workflow.id}
                onClick={() => setParams({ open: String(workflow.id), tab: 'overview' })}
              >
                <span className="resource-row__identity">
                  <span className="workflow-card__icon">
                    <WorkflowIcon size={19} />
                  </span>
                  <span>
                    <strong>{workflow.name}</strong>
                    <small>{workflow.description || 'No description'}</small>
                  </span>
                </span>
                <span className="workflow-card__mode">
                  {workflow.execution_mode === 'agentic' ? (
                    <GitBranch size={11} />
                  ) : (
                    <ListChecks size={11} />
                  )}
                  {workflow.execution_mode}
                </span>
                <span className="resource-row__facts">
                  <span>
                    {workflow.node_count} node{workflow.node_count === 1 ? '' : 's'}
                  </span>
                  <span>{workflow.model_name || 'No model selected'}</span>
                </span>
              </button>
            ))}
          </div>
          {!visible.length && (
            <div className="empty-state resource-search-empty">
              {query
                ? 'No matching workflows.'
                : 'No workflows yet. Create your first reusable workflow.'}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
