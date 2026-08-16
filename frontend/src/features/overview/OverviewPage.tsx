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
import type { BuiltinAgent, SubagentPlacement } from '../../api/types'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Loading } from '../../components/ui/Loading'
import {
  useAgentNodes,
  useAgents,
  useModels,
  useOverview,
  useServers,
  keys,
} from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'
import { AgentConfigDrawer } from './AgentConfigDrawer'
import { agentNodeTypes, type FlowData } from './AgentFlowNode'
import { AgentNodeDrawer } from './AgentNodeDrawer'
import { ConnectAgentModal } from './ConnectAgentModal'

type ConnectionParent = { nodeId: number | null; name: string }
type PendingDelete = { nodeId: number; name: string }

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
  const telegram = useQuery({ queryKey: keys.telegram, queryFn: api.telegram.get })
  const whatsapp = useQuery({ queryKey: keys.whatsapp, queryFn: api.whatsapp.get })
  const [selected, setSelected] = useState<BuiltinAgent | null>(null)
  const [modelId, setModelId] = useState<number>(0)
  const [supervisorOpen, setSupervisorOpen] = useState(false)
  const [supervisorModelId, setSupervisorModelId] = useState(0)
  const [connectionParent, setConnectionParent] = useState<ConnectionParent | null>(null)
  const [openNodeId, setOpenNodeId] = useState<number | null>(null)
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null)

  const save = useMutation({
    mutationFn: async () => {
      if (!selected || !modelId) return
      return api.overview.updateBuiltin(selected.key, { model_id: modelId })
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: keys.overview })
      setSelected(null)
    },
  })
  const toggleBuiltin = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      api.overview.updateBuiltin(key, { enabled }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: keys.overview }),
  })
  const saveSupervisor = useMutation({
    mutationFn: () => api.overview.updateSupervisor(supervisorModelId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: keys.overview })
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
  useEffect(() => {
    if (connectionParent) {
      createAgent.reset()
      connectAgent.reset()
    }
  }, [connectionParent])

  const graph = useMemo(() => {
    const info = overview.data
    const custom = agentNodes.data || []
    if (!info) return { nodes: [], edges: [] }
    const nodeWidth = 158
    const nodeGap = 14
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
    const layerCount = new Map<number, number>([[0, info.builtins.length]])
    occurrences.forEach(({ depth }) => {
      layerCount.set(depth, (layerCount.get(depth) || 0) + 1)
    })
    const widestLayer = Math.max(1, ...layerCount.values())
    const rowWidth = widestLayer * nodeWidth + Math.max(0, widestLayer - 1) * nodeGap
    const width = Math.max(900, rowWidth + 80)
    const center = width / 2
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
    const layerOffsets = new Map<number, number>()
    const nextPosition = (depth: number) => {
      const count = layerCount.get(depth) || 1
      const layerWidth = count * nodeWidth + Math.max(0, count - 1) * nodeGap
      const index = layerOffsets.get(depth) || 0
      layerOffsets.set(depth, index + 1)
      return {
        x: (width - layerWidth) / 2 + index * (nodeWidth + nodeGap),
        y: 345 + depth * 135,
      }
    }
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
            setModelId(item.model_id || 0)
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
    return { nodes, edges }
  }, [
    overview.data,
    agentNodes.data,
    telegram.data?.enabled,
    whatsapp.data?.enabled,
    navigate,
    deleteNode,
  ])

  if (overview.isLoading || agents.isLoading || agentNodes.isLoading)
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
        busy={createAgent.isPending || connectAgent.isPending}
        error={
          createAgent.error instanceof Error
            ? createAgent.error.message
            : connectAgent.error instanceof Error
              ? connectAgent.error.message
              : ''
        }
        onConnect={(subagentId) => connectAgent.mutate(subagentId)}
        onCreate={(body) => createAgent.mutate(body)}
        onClose={() => setConnectionParent(null)}
      />
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
      <AgentConfigDrawer
        open={Boolean(selected)}
        name={selected?.name || 'Agent'}
        description={selected?.description}
        agentKey={selected?.key}
        modelId={modelId}
        modelOptions={selected?.model_options}
        tools={selected?.tools}
        availability={
          selected
            ? {
                enabled: selected.enabled,
                onChange: (enabled) => {
                  toggleBuiltin.mutate({ key: selected.key, enabled })
                  setSelected({ ...selected, enabled })
                },
              }
            : undefined
        }
        modelHint="Any saved OpenAI-compatible model can power this agent."
        busy={save.isPending}
        error={save.error instanceof Error ? save.error.message : ''}
        onModelChange={setModelId}
        onSave={() => save.mutate()}
        onClose={() => setSelected(null)}
      />
      <AgentConfigDrawer
        open={supervisorOpen}
        name={overview.data?.supervisor.name || 'Mounir'}
        description={overview.data?.supervisor.description}
        modelId={supervisorModelId}
        modelOptions={overview.data?.supervisor.model_options}
        tools={overview.data?.supervisor.tools}
        capabilitiesLabel="Tools"
        modelHint="Any saved OpenAI-compatible model can power the supervisor."
        busy={saveSupervisor.isPending}
        error={saveSupervisor.error instanceof Error ? saveSupervisor.error.message : ''}
        onModelChange={setSupervisorModelId}
        onSave={() => saveSupervisor.mutate()}
        onClose={() => setSupervisorOpen(false)}
      />
    </>
  )
}
