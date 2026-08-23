import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import type { BuiltinAgent, SubagentPlacement, Workflow } from '../../api/types'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Loading } from '../../components/ui/Loading'
import {
  useAgentNodes,
  useAgents,
  useModels,
  useOverview,
  useServers,
  useSkills,
  useWorkflowNodes,
  useWorkflows,
  keys,
} from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'
import { agentNodeTypes, createLayeredFlowLayout, type FlowData } from './AgentFlowNode'
import { AgentNodeDrawer } from './AgentNodeDrawer'
import { BuiltinNodeDrawer } from './BuiltinNodeDrawer'
import { ConnectAgentModal, WorkflowPreviewModal } from './ConnectAgentModal'
import { SupervisorWizard } from './SupervisorWizard'

type ConnectionParent = { nodeId: number | null; name: string }
type PendingDelete = { nodeId: number; name: string }
type PendingWorkflowDelete = { nodeId: number; name: string }

function inputEdge(source: 'telegram' | 'whatsapp', color: string): Edge {
  return {
    id: `${source}-supervisor`,
    source,
    target: 'supervisor',
    type: 'smoothstep',
    className: 'flow-edge--input',
    markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
    style: { stroke: color, strokeWidth: 1.7 },
  }
}

export function OverviewPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const overview = useOverview()
  const agents = useAgents()
  const agentNodes = useAgentNodes()
  const models = useModels()
  const servers = useServers()
  const skills = useSkills()
  const workflows = useWorkflows()
  const workflowNodes = useWorkflowNodes()
  const telegram = useQuery({ queryKey: keys.telegram, queryFn: api.telegram.get })
  const whatsapp = useQuery({ queryKey: keys.whatsapp, queryFn: api.whatsapp.get })
  const [selected, setSelected] = useState<BuiltinAgent | null>(null)
  const [supervisorOpen, setSupervisorOpen] = useState(false)
  const [supervisorModelId, setSupervisorModelId] = useState(0)
  const [connectionParent, setConnectionParent] = useState<ConnectionParent | null>(null)
  const [openNodeId, setOpenNodeId] = useState<number | null>(null)
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null)
  const [pendingWorkflowDelete, setPendingWorkflowDelete] = useState<PendingWorkflowDelete | null>(
    null,
  )
  const [previewWorkflow, setPreviewWorkflow] = useState<Workflow | null>(null)

  const saveSupervisor = useMutation({
    mutationFn: (skillIds: number[]) =>
      api.overview.updateSupervisor({ model_id: supervisorModelId, skill_ids: skillIds }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.overview }),
        queryClient.invalidateQueries({ queryKey: keys.skills }),
      ])
      setSupervisorOpen(false)
    },
  })
  const createAgent = useMutation({
    mutationFn: (body: object) =>
      api.agents.create({ ...body, parent_node_id: connectionParent?.nodeId ?? null }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agents }),
        queryClient.invalidateQueries({ queryKey: keys.agentNodes }),
        queryClient.invalidateQueries({ queryKey: keys.skills }),
        queryClient.invalidateQueries({ queryKey: keys.overview }),
        queryClient.invalidateQueries({ queryKey: ['agent-node'] }),
      ])
      setConnectionParent(null)
    },
  })
  const connectAgent = useMutation({
    mutationFn: (subagentId: number) =>
      api.agentNodes.create({
        subagent_id: subagentId,
        parent_node_id: connectionParent?.nodeId ?? null,
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agents }),
        queryClient.invalidateQueries({ queryKey: keys.agentNodes }),
        queryClient.invalidateQueries({ queryKey: keys.overview }),
        queryClient.invalidateQueries({ queryKey: ['agent-node'] }),
      ])
      setConnectionParent(null)
    },
  })
  const connectWorkflow = useMutation({
    mutationFn: (workflowId: number) =>
      api.workflowNodes.create({
        child_workflow_id: workflowId,
        parent_node_id: connectionParent?.nodeId ?? null,
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.workflowNodes }),
        queryClient.invalidateQueries({ queryKey: keys.workflows }),
      ])
      setConnectionParent(null)
    },
  })
  const deleteNode = useMutation({
    mutationFn: (nodeId: number) => api.agentNodes.remove(nodeId),
    onSuccess: async () => {
      setOpenNodeId(null)
      setPendingDelete(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agents }),
        queryClient.invalidateQueries({ queryKey: keys.agentNodes }),
        queryClient.invalidateQueries({ queryKey: keys.overview }),
        queryClient.invalidateQueries({ queryKey: ['agent-node'] }),
      ])
    },
  })
  const deleteWorkflowNode = useMutation({
    mutationFn: (nodeId: number) => api.workflowNodes.remove(nodeId),
    onSuccess: async () => {
      setPendingWorkflowDelete(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.workflowNodes }),
        queryClient.invalidateQueries({ queryKey: keys.workflows }),
      ])
    },
  })
  useEffect(() => {
    if (connectionParent) {
      createAgent.reset()
      connectAgent.reset()
      connectWorkflow.reset()
    }
  }, [connectionParent])

  const graph = useMemo(() => {
    const info = overview.data
    const custom = agentNodes.data || []
    const reusableWorkflows = workflowNodes.data || []
    if (!info) return { nodes: [], edges: [] }
    type Occurrence = {
      item: SubagentPlacement
      nodeId: number
      id: string
      parentNodeId: string
      depth: number
      pathLabel: string
    }
    const occurrences: Occurrence[] = custom
      .map((item) => ({
        item,
        nodeId: item.node_id,
        id: `dynamic-agent-${item.node_id}`,
        parentNodeId:
          item.parent_node_id === null ? 'supervisor' : `dynamic-agent-${item.parent_node_id}`,
        depth: Math.max(0, (item.depth || 1) - 1),
        pathLabel: item.path_label || item.name,
      }))
      .sort(
        (left, right) => left.depth - right.depth || left.item.name.localeCompare(right.item.name),
      )
    const workflowDepths = reusableWorkflows.map((item) => {
      const parent = custom.find((node) => node.node_id === item.parent_node_id)
      return parent ? Math.max(0, parent.depth || 1) : 0
    })
    const { center, nextPosition } = createLayeredFlowLayout([
      ...info.builtins.map(() => 0),
      ...occurrences.map(({ depth }) => depth),
      ...workflowDepths,
    ])
    const nodes: Node<FlowData>[] = [
      {
        id: 'supervisor',
        type: 'agent',
        position: { x: center - 105, y: 145 },
        data: {
          label: info.supervisor.name,
          kind: 'supervisor',
          model: info.supervisor.model,
          onOpen: () => {
            setConnectionParent(null)
            setOpenNodeId(null)
            setSelected(null)
            setSupervisorModelId(info.supervisor.model_id || 0)
            setSupervisorOpen(true)
          },
          onAdd: () => {
            setOpenNodeId(null)
            setConnectionParent({ nodeId: null, name: info.supervisor.name })
          },
        },
      },
    ]
    const edges: Edge[] = []
    if (telegram.data?.enabled) {
      nodes.push({
        id: 'telegram',
        type: 'agent',
        position: { x: center - 342, y: 155 },
        data: {
          label: 'Telegram',
          kind: 'channel',
          channel: 'telegram',
          onOpen: () => navigate('/admin/telegram'),
        },
      })
      edges.push(inputEdge('telegram', '#2aabee'))
    }
    if (whatsapp.data?.enabled) {
      nodes.push({
        id: 'whatsapp',
        type: 'agent',
        position: { x: center - 342, y: 238 },
        data: {
          label: 'WhatsApp',
          kind: 'channel',
          channel: 'whatsapp',
          onOpen: () => navigate('/admin/whatsapp'),
        },
      })
      edges.push(inputEdge('whatsapp', '#25d366'))
    }
    info.builtins.forEach((item) => {
      const id = `builtin-${item.key}`
      nodes.push({
        id,
        type: 'agent',
        position: nextPosition(0),
        data: {
          label: item.name,
          kind: 'builtin',
          agentKey: item.key,
          enabled: item.enabled,
          model: item.model,
          onOpen: () => {
            setConnectionParent(null)
            setOpenNodeId(null)
            setSupervisorOpen(false)
            setSelected(item)
          },
        },
      })
      edges.push({
        id: `supervisor-${id}`,
        source: 'supervisor',
        target: id,
        animated: item.enabled,
        style: { stroke: item.enabled ? '#59c98e' : '#ff7d79' },
      })
    })
    occurrences.forEach(({ item, nodeId, id, parentNodeId, depth, pathLabel }) => {
      nodes.push({
        id,
        type: 'agent',
        position: nextPosition(depth),
        data: {
          label: item.name,
          kind: 'dynamic',
          enabled: Boolean(item.enabled),
          model: item.model || item.model_name,
          icon: item.has_icon ? `/api/subagents/${item.subagent_id}/icon` : undefined,
          onOpen: () => {
            setConnectionParent(null)
            setSelected(null)
            setSupervisorOpen(false)
            setOpenNodeId(nodeId)
          },
          onAdd:
            (item.depth || 1) < 4
              ? () => {
                  setOpenNodeId(null)
                  setConnectionParent({ nodeId, name: pathLabel })
                }
              : undefined,
          onDelete: () => {
            deleteNode.reset()
            setPendingDelete({ nodeId, name: item.name })
          },
        },
      })
      edges.push({
        id: `${parentNodeId}-${id}`,
        source: parentNodeId,
        target: id,
        animated: Boolean(item.enabled),
        style: { stroke: item.enabled ? '#79b8ff' : '#ff7d79' },
      })
    })
    reusableWorkflows.forEach((item) => {
      const parent = custom.find((node) => node.node_id === item.parent_node_id)
      const depth = parent ? Math.max(0, parent.depth || 1) : 0
      const id = `workflow-${item.id}`
      const parentId =
        item.parent_node_id === null ? 'supervisor' : `dynamic-agent-${item.parent_node_id}`
      nodes.push({
        id,
        type: 'agent',
        position: nextPosition(depth),
        data: {
          label: item.name,
          kind: 'workflow',
          workflowMode: item.execution_mode,
          onOpen: () => {
            setConnectionParent(null)
            setOpenNodeId(null)
            setSelected(null)
            setSupervisorOpen(false)
            setPreviewWorkflow(
              workflows.data?.find((workflow) => workflow.id === item.child_workflow_id) || null,
            )
          },
          onDelete: () => {
            deleteWorkflowNode.reset()
            setPendingWorkflowDelete({ nodeId: item.id, name: item.name })
          },
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
  }, [
    overview.data,
    agentNodes.data,
    workflowNodes.data,
    workflows.data,
    telegram.data?.enabled,
    whatsapp.data?.enabled,
    navigate,
    deleteNode,
    deleteWorkflowNode,
  ])

  if (
    overview.isLoading ||
    agents.isLoading ||
    agentNodes.isLoading ||
    workflows.isLoading ||
    workflowNodes.isLoading
  )
    return (
      <>
        <PageHeader
          title="Agent Overview"
          description="See how your assistant and specialists work together"
        />
        <Loading />
      </>
    )
  return (
    <>
      <PageHeader
        title="Agent Overview"
        description="See how your assistant and specialists work together"
      />
      <div className="page-content">
        <section className="card flow-card">
          <ReactFlow
            nodes={graph.nodes}
            edges={graph.edges}
            nodeTypes={agentNodeTypes}
            fitView
            minZoom={0.35}
            maxZoom={1.7}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} color="#587064" gap={18} size={1.35} />
            <Controls />
          </ReactFlow>
          <AgentNodeDrawer
            nodeId={openNodeId}
            selectorOpen={Boolean(connectionParent)}
            onAddSubagent={(nodeId, name) => {
              setOpenNodeId(null)
              setConnectionParent({ nodeId, name })
            }}
            onOpenSubagent={(subagentId) => navigate(`/admin/agents?open=${subagentId}`)}
            onClose={() => setOpenNodeId(null)}
          />
        </section>
      </div>
      <ConnectAgentModal
        open={Boolean(connectionParent)}
        parentNodeId={connectionParent?.nodeId ?? null}
        parentName={connectionParent?.name || 'Mounir'}
        models={models.data || []}
        servers={servers.data || []}
        agents={agents.data || []}
        placements={agentNodes.data || []}
        workflows={workflows.data || []}
        busy={createAgent.isPending || connectAgent.isPending || connectWorkflow.isPending}
        error={
          createAgent.error instanceof Error
            ? createAgent.error.message
            : connectAgent.error instanceof Error
              ? connectAgent.error.message
              : connectWorkflow.error instanceof Error
                ? connectWorkflow.error.message
                : ''
        }
        onConnect={(subagentId) => connectAgent.mutate(subagentId)}
        onConnectWorkflow={(workflowId) => connectWorkflow.mutate(workflowId)}
        onCreate={(body) => createAgent.mutate(body)}
        onClose={() => setConnectionParent(null)}
      />
      <WorkflowPreviewModal workflow={previewWorkflow} onClose={() => setPreviewWorkflow(null)} />
      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Disconnect subagent?"
        message={`Disconnect “${pendingDelete?.name || ''}” and its nested subagents from this workflow? Their saved configurations will remain available.`}
        confirmLabel="Disconnect"
        danger
        busy={deleteNode.isPending}
        error={deleteNode.error instanceof Error ? deleteNode.error.message : ''}
        onConfirm={() => pendingDelete && deleteNode.mutate(pendingDelete.nodeId)}
        onCancel={() => {
          if (deleteNode.isPending) return
          deleteNode.reset()
          setPendingDelete(null)
        }}
      />
      <ConfirmDialog
        open={Boolean(pendingWorkflowDelete)}
        title="Disconnect workflow?"
        message={`Disconnect “${pendingWorkflowDelete?.name || ''}” from this overview? The saved workflow will remain available.`}
        confirmLabel="Disconnect"
        danger
        busy={deleteWorkflowNode.isPending}
        error={deleteWorkflowNode.error instanceof Error ? deleteWorkflowNode.error.message : ''}
        onConfirm={() =>
          pendingWorkflowDelete && deleteWorkflowNode.mutate(pendingWorkflowDelete.nodeId)
        }
        onCancel={() => {
          if (deleteWorkflowNode.isPending) return
          deleteWorkflowNode.reset()
          setPendingWorkflowDelete(null)
        }}
      />
      <BuiltinNodeDrawer
        agent={selected}
        onOpenSubagent={(agentKey) =>
          navigate(`/admin/agents?view=built-in&builtin=${encodeURIComponent(agentKey)}`)
        }
        onClose={() => setSelected(null)}
      />
      <SupervisorWizard
        open={supervisorOpen}
        supervisor={overview.data?.supervisor}
        modelId={supervisorModelId}
        skills={skills.data || []}
        skillsLoading={skills.isLoading}
        skillsError={skills.error instanceof Error ? skills.error.message : ''}
        busy={saveSupervisor.isPending}
        error={saveSupervisor.error instanceof Error ? saveSupervisor.error.message : ''}
        onModelChange={setSupervisorModelId}
        onSave={(skillIds) => saveSupervisor.mutate(skillIds)}
        onClose={() => setSupervisorOpen(false)}
      />
    </>
  )
}
