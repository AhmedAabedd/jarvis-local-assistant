import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import {
  BookOpen,
  Bot,
  GitBranch,
  Image as ImageIcon,
  ListChecks,
  Plus,
  Settings,
  Workflow,
  X,
} from 'lucide-react'

export type FlowData = {
  label: string
  kind: string
  model?: string
  workflowMode?: 'agentic' | 'direct'
  enabled?: boolean
  icon?: string
  agentKey?: string
  channel?: 'telegram' | 'whatsapp'
  flowDirection?: 'horizontal' | 'vertical'
  onOpen?: () => void
  onAdd?: () => void
  onDelete?: () => void
}

export function createLayeredFlowLayout(depths: number[]) {
  const nodeWidth = 196
  const nodeGap = 14
  const layerCount = new Map<number, number>()
  depths.forEach((depth) => layerCount.set(depth, (layerCount.get(depth) || 0) + 1))
  const widestLayer = Math.max(1, ...layerCount.values())
  const rowWidth = widestLayer * nodeWidth + Math.max(0, widestLayer - 1) * nodeGap
  const width = Math.max(900, rowWidth + 80)
  const layerOffsets = new Map<number, number>()

  return {
    center: width / 2,
    nextPosition: (depth: number) => {
      const count = layerCount.get(depth) || 1
      const layerWidth = count * nodeWidth + Math.max(0, count - 1) * nodeGap
      const index = layerOffsets.get(depth) || 0
      layerOffsets.set(depth, index + 1)
      return {
        x: (width - layerWidth) / 2 + index * (nodeWidth + nodeGap),
        y: 345 + depth * 135,
      }
    },
  }
}

function TelegramIcon() {
  return (
    <svg width="23" height="23" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="currentColor"
        d="M21.6 3.2 2.8 10.5c-1.3.5-1.3 1.2-.2 1.6l4.8 1.5 1.8 5.5c.2.7.1.9.8.9.5 0 .8-.2 1-.4l2.4-2.3 5 3.7c.9.5 1.6.3 1.8-.9l3.1-14.8c.4-1.5-.5-2.2-1.7-1.7ZM9.3 13.3l9.4-5.9c.5-.3.9-.1.5.2l-7.8 7-.3 3.1-1.8-4.4Z"
      />
    </svg>
  )
}

function ModelIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4 7h16M4 12h16M4 17h10"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function AgentFlowNode({ data }: NodeProps<Node<FlowData>>) {
  const channelClass = data.channel ? `flow-node--${data.channel}` : ''
  const horizontal = data.flowDirection === 'horizontal'
  const endpoint = data.kind === 'workflow-start' || data.kind === 'workflow-end'
  const WorkflowModeIcon = data.workflowMode === 'direct' ? ListChecks : GitBranch
  const Icon =
    data.kind === 'supervisor'
      ? Bot
      : data.kind === 'workflow'
        ? Workflow
        : data.agentKey === 'media'
          ? ImageIcon
          : data.agentKey === 'knowledge'
            ? BookOpen
            : data.agentKey === 'system'
              ? Settings
              : Bot

  return (
    <div
      className={`flow-node flow-node--${data.kind} ${channelClass} ${data.enabled === false ? 'flow-node--inactive' : ''}`}
    >
      {data.kind !== 'channel' &&
        data.kind !== 'workflow-start' &&
        data.kind !== 'orchestrator' && (
          <Handle
            type="target"
            position={
              horizontal || data.kind === 'workflow-end' || data.kind === 'supervisor'
                ? Position.Left
                : Position.Top
            }
          />
        )}
      <button
        className="flow-node__content"
        onClick={data.onOpen}
        aria-label={data.label}
        title={data.kind === 'channel' ? data.label : undefined}
      >
        {data.channel === 'telegram' ? (
          <TelegramIcon />
        ) : data.channel === 'whatsapp' ? (
          <img className="flow-node__channel-icon" src="/images/whatsapp.svg" alt="" />
        ) : (
          <>
            <div className={`flow-node__head ${endpoint ? 'flow-node__head--endpoint' : ''}`}>
              {!endpoint && (
                <span className={`avatar ${data.icon ? 'has-image' : ''}`}>
                  {data.icon ? <img src={data.icon} alt="" /> : <Icon size={16} />}
                </span>
              )}
              <span className="flow-node__identity">
                {data.kind === 'supervisor' && <span className="flow-node__kind">Supervisor</span>}
                <span className="flow-node__name">{data.label}</span>
              </span>
            </div>
            {data.workflowMode ? (
              <div
                className={`flow-node__workflow-mode flow-node__workflow-mode--${data.workflowMode}`}
                title={`${data.workflowMode === 'direct' ? 'Direct' : 'Agentic'} workflow`}
              >
                <WorkflowModeIcon size={11} />
                <span>{data.workflowMode === 'direct' ? 'Direct' : 'Agentic'}</span>
              </div>
            ) : data.model ? (
              <div className="flow-node__model" title={data.model}>
                <ModelIcon />
                <span>{data.model}</span>
              </div>
            ) : null}
          </>
        )}
      </button>
      {(data.kind === 'dynamic' || data.kind === 'workflow') && data.onDelete && (
        <button
          className="flow-node__disconnect nodrag nopan"
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            data.onDelete?.()
          }}
          aria-label={`Disconnect ${data.label}`}
          title="Disconnect node"
        >
          <X size={14} />
        </button>
      )}
      {data.onAdd && (
        <button
          className="flow-node__connect nodrag nopan"
          onClick={(event) => {
            event.stopPropagation()
            data.onAdd?.()
          }}
          aria-label={`Add a node under ${data.label}`}
          title="Add node"
        >
          <Plus size={10} strokeWidth={2.5} />
        </button>
      )}
      {(data.kind === 'supervisor' ||
        data.kind === 'channel' ||
        data.kind === 'dynamic' ||
        data.kind === 'orchestrator' ||
        data.kind === 'workflow-start' ||
        (horizontal && data.kind !== 'workflow-end')) && (
        <Handle
          type="source"
          position={data.kind === 'channel' || horizontal ? Position.Right : Position.Bottom}
        />
      )}
    </div>
  )
}

export const agentNodeTypes = { agent: AgentFlowNode }
