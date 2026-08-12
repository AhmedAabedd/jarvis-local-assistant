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
import type { BuiltinAgent, Subagent } from '../../api/types'
import { Loading } from '../../components/ui/Loading'
import { useAgents, useOverview, keys } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'
import { AgentConfigModal } from './AgentConfigModal'
import { agentNodeTypes, type FlowData } from './AgentFlowNode'
import { ConnectAgentModal } from './ConnectAgentModal'

type ConnectionParent = { nodeId: number | null; agentId: number | null; name: string }

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
  const telegram = useQuery({ queryKey: keys.telegram, queryFn: api.telegram.get })
  const whatsapp = useQuery({ queryKey: keys.whatsapp, queryFn: api.whatsapp.get })
  const [selected, setSelected] = useState<BuiltinAgent | null>(null)
  const [modelId, setModelId] = useState<number>(0)
  const [supervisorOpen, setSupervisorOpen] = useState(false)
  const [supervisorModelId, setSupervisorModelId] = useState(0)
  const [connectionParent, setConnectionParent] = useState<ConnectionParent | null>(null)

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
  const connectAgent = useMutation({
    mutationFn: ({ agent, parentNodeId }: { agent: Subagent; parentNodeId: number | null }) =>
      api.agents.connect(agent.id, parentNodeId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.agents }),
        queryClient.invalidateQueries({ queryKey: keys.overview }),
      ])
      setConnectionParent(null)
    },
  })
  useEffect(() => {
    if (connectionParent) connectAgent.reset()
  }, [connectionParent])

  const graph = useMemo(() => {
    const info = overview.data
    const custom = agents.data || []
    if (!info) return { nodes: [], edges: [] }
    const nodeWidth = 158
    const nodeGap = 14
    type Occurrence = {
      item: Subagent
      nodeId: number
      id: string
      parentNodeId: string
      depth: number
      pathLabel: string
    }
    const occurrences: Occurrence[] = custom
      .flatMap((item) =>
        (item.placements || []).map((placement) => ({
          item,
          nodeId: placement.id,
          id: `dynamic-node-${placement.id}`,
          parentNodeId:
            placement.parent_node_id === null
              ? 'supervisor'
              : `dynamic-node-${placement.parent_node_id}`,
          depth: Math.max(0, placement.depth - 1),
          pathLabel: placement.path_label,
        })),
      )
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
            setSupervisorModelId(info.supervisor.model_id || 0)
            setSupervisorOpen(true)
          },
          onConnect: () =>
            setConnectionParent({ nodeId: null, agentId: null, name: info.supervisor.name }),
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
          icon: item.has_icon ? `/api/subagents/${item.id}/icon` : undefined,
          onOpen: () => navigate(`/admin/agents?open=${item.id}`),
          onConnect: () => setConnectionParent({ nodeId, agentId: item.id, name: pathLabel }),
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
  }, [overview.data, agents.data, telegram.data?.enabled, whatsapp.data?.enabled, navigate])

  if (overview.isLoading || agents.isLoading)
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
        </section>
      </div>
      <ConnectAgentModal
        open={Boolean(connectionParent)}
        parentNodeId={connectionParent?.nodeId ?? null}
        parentAgentId={connectionParent?.agentId ?? null}
        parentName={connectionParent?.name || 'Mounir'}
        agents={agents.data || []}
        busy={connectAgent.isPending}
        error={connectAgent.error instanceof Error ? connectAgent.error.message : ''}
        onSelect={(agent) =>
          connectAgent.mutate({ agent, parentNodeId: connectionParent?.nodeId ?? null })
        }
        onClose={() => setConnectionParent(null)}
      />
      <AgentConfigModal
        open={Boolean(selected)}
        name={selected?.name || 'Agent'}
        description={selected?.description}
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
        modelHint="Only models compatible with this agent are shown."
        busy={save.isPending}
        error={save.error instanceof Error ? save.error.message : ''}
        onModelChange={setModelId}
        onSave={() => save.mutate()}
        onClose={() => setSelected(null)}
      />
      <AgentConfigModal
        open={supervisorOpen}
        name={overview.data?.supervisor.name || 'Mounir'}
        description={overview.data?.supervisor.description}
        modelId={supervisorModelId}
        modelOptions={overview.data?.supervisor.model_options}
        tools={overview.data?.supervisor.tools}
        capabilitiesLabel="Direct capabilities"
        modelHint="The primary model used to understand and route requests."
        busy={saveSupervisor.isPending}
        error={saveSupervisor.error instanceof Error ? saveSupervisor.error.message : ''}
        onModelChange={setSupervisorModelId}
        onSave={() => saveSupervisor.mutate()}
        onClose={() => setSupervisorOpen(false)}
      />
    </>
  )
}
