import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronRight, Cpu, Pencil, Plus, RefreshCw, Save, Search, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../../api/client'
import type { SubagentNodeRelation } from '../../api/types'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Loading } from '../../components/ui/Loading'
import { ToolChoices } from '../resources/ToolChoices'
import {
  expandLegacyToolRules,
  selectedToolOptions,
  useMcpToolCatalog,
} from '../resources/McpToolAccess'

interface Props {
  nodeId: number | null
  selectorOpen: boolean
  onAddSubagent: (nodeId: number, name: string) => void
  onOpenSubagent: (subagentId: number) => void
  onClose: () => void
}

function RelationCard({
  node,
  label,
  meta = 'Subagent',
  onRemove,
}: {
  node?: SubagentNodeRelation
  label?: string
  meta?: string
  onRemove?: () => void
}) {
  return (
    <div className="node-relation node-relation--static">
      <span className={`avatar ${node?.has_icon ? 'has-image' : ''}`}>
        {node?.has_icon ? (
          <img src={`/api/subagents/${node.subagent_id}/icon`} alt="" />
        ) : (
          <Bot size={16} />
        )}
      </span>
      <span className="node-relation__copy">
        <strong>{node?.name || label || 'Mounir'}</strong>
        <small>{meta}</small>
      </span>
      {onRemove && node && (
        <button
          className="node-relation__remove"
          type="button"
          aria-label={`Disconnect ${node.name}`}
          title="Disconnect subagent"
          onClick={onRemove}
        >
          <X size={13} />
        </button>
      )}
    </div>
  )
}

export function AgentNodeDrawer({
  nodeId,
  selectorOpen,
  onAddSubagent,
  onOpenSubagent,
  onClose,
}: Props) {
  const queryClient = useQueryClient()
  const [selectedTools, setSelectedTools] = useState<Set<string>>(new Set())
  const [toolSearch, setToolSearch] = useState('')
  const [editingTools, setEditingTools] = useState(false)
  const [pendingRemoval, setPendingRemoval] = useState<SubagentNodeRelation | null>(null)
  const node = useQuery({
    queryKey: ['agent-node', nodeId],
    queryFn: () => api.agentNodes.get(nodeId as number),
    enabled: nodeId !== null,
  })
  const sources = useMemo(() => node.data?.subagent.mcp_sources || [], [node.data])
  const sourceServers = useMemo(
    () => sources.map((source) => ({
      id: Number(source.mcp_server_id),
      name: source.mcp_server_name || 'MCP server',
      connection_status: source.connection_status,
    })),
    [sources],
  )
  const toolGroups = useMcpToolCatalog(sourceServers)
  const availableTools = useMemo(
    () => selectedToolOptions(toolGroups, sources),
    [toolGroups, sources],
  )
  const availableNames = useMemo(() => availableTools.map((tool) => tool.name), [availableTools])
  const availableSignature = availableNames.join('\u0000')
  const filteredTools = useMemo(() => {
    const query = toolSearch.trim().toLowerCase()
    if (!query) return availableTools
    return availableTools.filter(
      (tool) =>
        (tool.label || tool.name).toLowerCase().includes(query) ||
        (tool.server_name || '').toLowerCase().includes(query) ||
        (tool.description || '').toLowerCase().includes(query),
    )
  }, [availableTools, toolSearch])

  useEffect(() => {
    if (!node.data) return
    setSelectedTools(
      node.data.enabled_tools === null
        ? new Set(availableNames)
        : expandLegacyToolRules(node.data.enabled_tools, availableTools),
    )
    setToolSearch('')
    setEditingTools(false)
  }, [node.data, availableSignature])

  const savedSelection = useMemo(
    () =>
      node.data?.enabled_tools == null
        ? new Set(availableNames)
        : expandLegacyToolRules(node.data.enabled_tools, availableTools),
    [node.data, availableSignature],
  )
  const selectionChanged =
    selectedTools.size !== savedSelection.size ||
    [...selectedTools].some((name) => !savedSelection.has(name))
  const saveTools = useMutation({
    mutationFn: () => {
      const everyAvailableToolEnabled =
        availableNames.length > 0 &&
        selectedTools.size === availableNames.length &&
        availableNames.every((name) => selectedTools.has(name))
      return api.agentNodes.update(nodeId as number, {
        enabled_tools: everyAvailableToolEnabled ? null : [...selectedTools],
      })
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(['agent-node', nodeId], saved)
      queryClient.invalidateQueries({ queryKey: ['agents'] })
      queryClient.invalidateQueries({ queryKey: ['agent-nodes'] })
      setEditingTools(false)
    },
  })
  const refreshTools = useMutation({
    mutationFn: () => Promise.all(
      sourceServers.map((server) => api.servers.test(Number(server.id))),
    ),
    onSuccess: (states) => states.forEach((state, index) => {
      queryClient.setQueryData(['server-tools', sourceServers[index].id], state)
    }),
  })
  const removeChild = useMutation({
    mutationFn: (childId: number) => api.agentNodes.remove(childId),
    onSuccess: async () => {
      setPendingRemoval(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agent-node', nodeId] }),
        queryClient.invalidateQueries({ queryKey: ['agents'] }),
        queryClient.invalidateQueries({ queryKey: ['agent-nodes'] }),
        queryClient.invalidateQueries({ queryKey: ['overview'] }),
      ])
    },
  })

  useEffect(() => {
    if (nodeId === null || pendingRemoval || selectorOpen) return
    const closeOnEscape = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [nodeId, onClose, pendingRemoval, selectorOpen])

  if (nodeId === null) return null

  const placement = node.data
  const subagent = placement?.subagent
  const catalogError = toolGroups.find((group) => group.error)?.error
  const toolsLoading = toolGroups.some((group) => group.loading)
  const toolError = catalogError || refreshTools.error || saveTools.error
  const visibleTools = editingTools
    ? filteredTools
    : availableTools.filter((tool) => selectedTools.has(tool.name))
  return createPortal(
    <>
      <aside className="node-drawer" role="dialog" aria-modal="false" aria-labelledby="node-title">
        <header className="node-drawer__compact-header">
          {subagent && (
            <a
              className="node-drawer__open-link"
              href={`/admin/agents?open=${subagent.id}`}
              aria-label="Open subagent"
              onClick={(event) => {
                event.preventDefault()
                onOpenSubagent(subagent.id)
              }}
            >
              Open subagent <ChevronRight size={13} aria-hidden="true" />
            </a>
          )}
          <button
            className="icon-button panel-close-button"
            type="button"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={14} />
          </button>
        </header>
        <div className="node-drawer__body">
          {node.isLoading ? (
            <Loading label="Opening subagent…" />
          ) : node.error || !placement || !subagent ? (
            <Feedback
              message={node.error instanceof Error ? node.error.message : 'Subagent not found.'}
            />
          ) : (
            <>
              <div className="node-detail-identity">
                <span className={`avatar ${subagent.has_icon ? 'has-image' : ''}`}>
                  {subagent.has_icon ? (
                    <img src={`/api/subagents/${subagent.id}/icon`} alt="" />
                  ) : (
                    <Bot size={18} />
                  )}
                </span>
                <span>
                  <strong id="node-title">{subagent.name}</strong>
                  {subagent.description && <small>{subagent.description}</small>}
                </span>
              </div>
              <div className="node-detail-model">
                <Cpu size={12} />
                <small>Model</small>
                <strong title={subagent.model}>{subagent.model || subagent.model_name}</strong>
              </div>

              <section className="node-connections">
                <div className="node-connection-group">
                  <h3>Parent</h3>
                  {placement.parent ? (
                    <RelationCard node={placement.parent} />
                  ) : (
                    <RelationCard label="Mounir" meta="Orchestrator" />
                  )}
                </div>

                <div className="node-connection-group">
                  <h3>Subagents</h3>
                  {placement.children.length ? (
                    <div className="node-relations">
                      {placement.children.map((child) => (
                        <RelationCard
                          key={child.id}
                          node={child}
                          onRemove={() => {
                            removeChild.reset()
                            setPendingRemoval(child)
                          }}
                        />
                      ))}
                    </div>
                  ) : placement.depth < 4 ? (
                    <button
                      className="node-empty-connect"
                      type="button"
                      onClick={() => onAddSubagent(placement.id, placement.path_label)}
                    >
                      <Plus size={12} />
                      <span>Add subagent</span>
                    </button>
                  ) : (
                    <small className="node-empty-message">Maximum nesting depth reached.</small>
                  )}
                </div>
              </section>

              <div className="node-tools">
                <div className="node-tools__title">
                  <span className="field__label">Tools</span>
                  <div className="node-tools__header-actions">
                    <button
                      className="node-tools__icon-button"
                      type="button"
                      onClick={() => refreshTools.mutate()}
                      disabled={refreshTools.isPending || !sourceServers.length}
                      aria-label="Refresh tools"
                      title="Refresh tools"
                    >
                      {refreshTools.isPending ? (
                        <span className="spinner" />
                      ) : (
                        <RefreshCw size={12} />
                      )}
                    </button>
                    {!editingTools && availableTools.length > 0 && (
                      <button
                        className="node-tools__icon-button"
                        type="button"
                        onClick={() => setEditingTools(true)}
                        aria-label="Edit tool access"
                        title="Edit tool access"
                      >
                        <Pencil size={12} />
                      </button>
                    )}
                    {editingTools && (
                      <>
                        <button
                          className="node-tools__icon-button"
                          type="button"
                          disabled={saveTools.isPending}
                          onClick={() => {
                            setSelectedTools(new Set(savedSelection))
                            setToolSearch('')
                            setEditingTools(false)
                          }}
                          aria-label="Cancel tool changes"
                          title="Cancel"
                        >
                          <X size={13} />
                        </button>
                        {selectionChanged && (
                          <button
                            className="node-tools__icon-button node-tools__save-button"
                            type="button"
                            disabled={saveTools.isPending}
                            onClick={() => saveTools.mutate()}
                            aria-label="Save tool access"
                            title="Save"
                          >
                            {saveTools.isPending ? (
                              <span className="spinner" />
                            ) : (
                              <Save size={13} />
                            )}
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>

                {toolsLoading ? (
                  <Loading label="Loading tools…" />
                ) : (
                  <>
                    {editingTools && availableTools.length > 0 && (
                      <>
                        <label className="node-tool-search">
                          <Search size={13} />
                          <input
                            value={toolSearch}
                            onChange={(event) => setToolSearch(event.target.value)}
                            placeholder="Search tools"
                          />
                        </label>
                        <div className="node-tool-actions">
                          <button
                            type="button"
                            onClick={() => setSelectedTools(new Set(availableNames))}
                          >
                            Enable all
                          </button>
                          <button type="button" onClick={() => setSelectedTools(new Set())}>
                            Disable all
                          </button>
                        </div>
                      </>
                    )}
                    <div className="node-tool-list agent-config-drawer__capabilities">
                      <ToolChoices
                        tools={visibleTools}
                        selected={selectedTools}
                        onChange={setSelectedTools}
                        readOnly={!editingTools}
                        empty={
                          editingTools && availableTools.length
                            ? 'No tools match this search.'
                            : availableTools.length
                              ? 'No tools enabled.'
                              : sources.length
                                ? 'Refresh the MCP connections to discover their tools.'
                                : 'This is a prompt-only subagent with no external tools.'
                        }
                      />
                    </div>
                    <Feedback
                      message={toolError instanceof Error ? toolError.message : toolError || undefined}
                    />
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </aside>
      <ConfirmDialog
        open={Boolean(pendingRemoval)}
        title="Disconnect subagent?"
        message={`Disconnect “${pendingRemoval?.name || ''}” and its nested subagents from this workflow? Their saved configurations will remain available.`}
        confirmLabel="Disconnect"
        danger
        busy={removeChild.isPending}
        error={removeChild.error instanceof Error ? removeChild.error.message : ''}
        onConfirm={() => pendingRemoval && removeChild.mutate(pendingRemoval.id)}
        onCancel={() => {
          if (removeChild.isPending) return
          removeChild.reset()
          setPendingRemoval(null)
        }}
      />
    </>,
    document.body,
  )
}
