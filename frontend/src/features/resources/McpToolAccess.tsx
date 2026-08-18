import { useQueries, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { McpServer, SubagentMcpSource, ToolInfo } from '../../api/types'
import { readable } from './helpers'

export type McpToolGroup = {
  server: Pick<McpServer, 'id' | 'name' | 'connection_status'>
  tools: ToolInfo[]
  loading: boolean
  error: string
}

export function toolAccessKey(serverId: number, toolName: string) {
  return `${serverId}:${toolName}`
}

export function useMcpToolCatalog(
  servers: Array<Pick<McpServer, 'id' | 'name' | 'connection_status'>>,
) {
  const queries = useQueries({
    queries: servers.map((server) => ({
      queryKey: ['server-tools', server.id],
      queryFn: () => api.servers.tools(server.id),
      staleTime: 30_000,
    })),
  })
  return useMemo<McpToolGroup[]>(
    () =>
      servers.map((server, index) => ({
        server,
        tools: queries[index]?.data?.tools || [],
        loading: Boolean(queries[index]?.isLoading),
        error: queries[index]?.error instanceof Error ? queries[index].error.message : '',
      })),
    [queries, servers],
  )
}

export function selectedToolOptions(groups: McpToolGroup[], sources: SubagentMcpSource[]) {
  const byServer = new Map(sources.map((source) => [Number(source.mcp_server_id), source]))
  return groups.flatMap((group) => {
    const source = byServer.get(Number(group.server.id))
    if (!source) return []
    const allowed = source.enabled_tools === null ? null : new Set(source.enabled_tools)
    return group.tools
      .filter((tool) => allowed === null || allowed.has(tool.name))
      .map((tool) => ({
        ...tool,
        name: toolAccessKey(Number(group.server.id), tool.name),
        tool_name: tool.name,
        label: readable(tool.name),
        server_name: group.server.name,
      }))
  })
}

export function expandLegacyToolRules(rules: string[], options: ToolInfo[]) {
  const names = new Set(options.map((tool) => tool.name))
  return new Set(
    rules.flatMap((rule) => {
      if (rule === '*' || names.has(rule)) return [rule]
      const matches = options.filter((tool) => tool.tool_name === rule).map((tool) => tool.name)
      return matches.length ? matches : [rule]
    }),
  )
}

export function MultiServerToolPicker({
  servers,
  groups,
  sources,
  onChange,
}: {
  servers: McpServer[]
  groups: McpToolGroup[]
  sources: SubagentMcpSource[]
  onChange: (sources: SubagentMcpSource[]) => void
}) {
  const queryClient = useQueryClient()
  const [serverQuery, setServerQuery] = useState('')
  const [toolQuery, setToolQuery] = useState('')
  const [activeServerId, setActiveServerId] = useState<number | null>(() => {
    const initialId = sources[0]?.mcp_server_id ?? servers[0]?.id
    return initialId === undefined ? null : Number(initialId)
  })
  const [refreshing, setRefreshing] = useState(new Set<number>())
  const [refreshErrors, setRefreshErrors] = useState(new Map<number, string>())
  const selectedByServer = useMemo(
    () => new Map(sources.map((source) => [Number(source.mcp_server_id), source])),
    [sources],
  )
  const serverDetails = useMemo(
    () => new Map(servers.map((server) => [Number(server.id), server])),
    [servers],
  )
  const normalizedServerQuery = serverQuery.trim().toLocaleLowerCase()
  const visibleGroups = useMemo(
    () =>
      groups.filter((group) => {
        if (!normalizedServerQuery) return true
        const details = serverDetails.get(Number(group.server.id))
        return (
          group.server.name.toLocaleLowerCase().includes(normalizedServerQuery) ||
          (details?.description || '').toLocaleLowerCase().includes(normalizedServerQuery)
        )
      }),
    [groups, normalizedServerQuery, serverDetails],
  )
  const activeGroup = useMemo(
    () => groups.find((group) => Number(group.server.id) === activeServerId),
    [activeServerId, groups],
  )
  const activeSource = activeServerId === null ? undefined : selectedByServer.get(activeServerId)
  const activeSelection = useMemo(
    () =>
      new Set(
        activeSource?.enabled_tools === null
          ? activeGroup?.tools.map((tool) => tool.name) || []
          : activeSource?.enabled_tools || [],
      ),
    [activeGroup, activeSource],
  )
  const activeEnabledCount = useMemo(
    () => activeGroup?.tools.filter((tool) => activeSelection.has(tool.name)).length || 0,
    [activeGroup, activeSelection],
  )
  const allActiveToolsEnabled = Boolean(
    activeGroup?.tools.length && activeEnabledCount === activeGroup.tools.length,
  )
  const normalizedToolQuery = toolQuery.trim().toLocaleLowerCase()
  const visibleTools = useMemo(
    () =>
      (activeGroup?.tools || []).filter(
        (tool) =>
          !normalizedToolQuery ||
          readable(tool.name).toLocaleLowerCase().includes(normalizedToolQuery) ||
          tool.name.toLocaleLowerCase().includes(normalizedToolQuery) ||
          (tool.description || '').toLocaleLowerCase().includes(normalizedToolQuery),
      ),
    [activeGroup, normalizedToolQuery],
  )

  useEffect(() => {
    if (
      activeServerId !== null &&
      groups.some((group) => Number(group.server.id) === activeServerId)
    ) {
      return
    }
    const selectedGroup = groups.find((group) => selectedByServer.has(Number(group.server.id)))
    const nextId = selectedGroup?.server.id ?? groups[0]?.server.id
    setActiveServerId(nextId === undefined ? null : Number(nextId))
  }, [activeServerId, groups, selectedByServer])

  useEffect(() => setToolQuery(''), [activeServerId])

  const replaceSource = (serverId: number, enabledTools: string[] | null | undefined) => {
    const index = sources.findIndex((source) => Number(source.mcp_server_id) === serverId)
    if (enabledTools === undefined) {
      return onChange(sources.filter((source) => Number(source.mcp_server_id) !== serverId))
    }
    const next = [...sources]
    const replacement = { mcp_server_id: serverId, enabled_tools: enabledTools }
    if (index >= 0) next[index] = replacement
    else next.push(replacement)
    onChange(next)
  }

  const toggleTool = (group: McpToolGroup, toolName: string, checked: boolean) => {
    const serverId = Number(group.server.id)
    const current = selectedByServer.get(serverId)
    const selected = new Set(
      current?.enabled_tools === null
        ? group.tools.map((tool) => tool.name)
        : current?.enabled_tools || [],
    )
    checked ? selected.add(toolName) : selected.delete(toolName)
    if (!selected.size) return replaceSource(serverId, undefined)
    if (selected.size === group.tools.length) return replaceSource(serverId, null)
    replaceSource(serverId, [...selected])
  }

  const refresh = async (serverId: number) => {
    setRefreshing((current) => new Set(current).add(serverId))
    setRefreshErrors((current) => {
      const next = new Map(current)
      next.delete(serverId)
      return next
    })
    try {
      const state = await api.servers.test(serverId)
      queryClient.setQueryData(['server-tools', serverId], state)
    } catch (cause) {
      setRefreshErrors((current) =>
        new Map(current).set(
          serverId,
          cause instanceof Error ? cause.message : 'Could not refresh this MCP server.',
        ),
      )
    } finally {
      setRefreshing((current) => {
        const next = new Set(current)
        next.delete(serverId)
        return next
      })
    }
  }

  if (!servers.length) {
    return (
      <div className="empty-state mcp-access__empty">
        No MCP servers are connected. This subagent will run with its model and prompt only.
      </div>
    )
  }

  return (
    <div className="mcp-access">
      <aside className="mcp-access__sidebar">
        <strong className="mcp-access__sidebar-title">MCP servers</strong>
        <label className="resource-search mcp-access__search">
          <Search size={13} />
          <input
            type="search"
            value={serverQuery}
            onChange={(event) => setServerQuery(event.target.value)}
            placeholder="Search servers…"
            aria-label="Search MCP servers"
          />
        </label>
        <div className="mcp-access__server-list">
          {visibleGroups.length ? (
            visibleGroups.map((group) => {
              const serverId = Number(group.server.id)
              return (
                <button
                  className={`mcp-access__server-item ${activeServerId === serverId ? 'is-active' : ''}`}
                  type="button"
                  key={serverId}
                  aria-pressed={activeServerId === serverId}
                  onClick={() => setActiveServerId(serverId)}
                >
                  {group.server.name}
                </button>
              )
            })
          ) : (
            <div className="mcp-access__message">No MCP servers match this search.</div>
          )}
        </div>
      </aside>

      <section className="mcp-access__detail">
        {activeGroup ? (
          <>
            <header className="mcp-access__detail-header">
              <strong>{activeGroup.server.name}</strong>
              <div className="mcp-access__counts">
                <strong>{activeEnabledCount} tools enabled</strong>
                <span>{activeGroup.tools.length} available</span>
              </div>
              <button
                type="button"
                title="Refresh discovered tools"
                aria-label={`Refresh tools from ${activeGroup.server.name}`}
                disabled={refreshing.has(Number(activeGroup.server.id))}
                onClick={() => refresh(Number(activeGroup.server.id))}
              >
                <RefreshCw
                  size={13}
                  className={refreshing.has(Number(activeGroup.server.id)) ? 'spin' : ''}
                />
              </button>
            </header>

            <div className="mcp-access__tool-toolbar">
              <label className="resource-search mcp-access__search">
                <Search size={13} />
                <input
                  type="search"
                  value={toolQuery}
                  onChange={(event) => setToolQuery(event.target.value)}
                  placeholder="Search tools…"
                  aria-label={`Search tools from ${activeGroup.server.name}`}
                />
              </label>
              <button
                className={`mcp-access__select-all ${allActiveToolsEnabled ? 'is-active' : ''}`}
                type="button"
                disabled={!activeGroup.tools.length}
                aria-pressed={allActiveToolsEnabled}
                aria-label={`${allActiveToolsEnabled ? 'Disable' : 'Enable'} all tools from ${activeGroup.server.name}`}
                title={allActiveToolsEnabled ? 'Disable all tools' : 'Enable all tools'}
                onClick={() =>
                  replaceSource(
                    Number(activeGroup.server.id),
                    allActiveToolsEnabled ? undefined : null,
                  )
                }
              >
                Select all
              </button>
            </div>

            <div className="mcp-access__tool-list">
              {activeGroup.loading ? (
                <div className="mcp-access__message">Loading discovered tools…</div>
              ) : activeGroup.error || refreshErrors.get(Number(activeGroup.server.id)) ? (
                <div className="mcp-access__message is-error">
                  {activeGroup.error || refreshErrors.get(Number(activeGroup.server.id))}
                </div>
              ) : visibleTools.length ? (
                visibleTools.map((tool) => (
                  <label key={tool.name}>
                    <input
                      type="checkbox"
                      checked={activeSelection.has(tool.name)}
                      onChange={(event) => toggleTool(activeGroup, tool.name, event.target.checked)}
                    />
                    <span>
                      <strong>{readable(tool.name)}</strong>
                      <small>{tool.description || 'No description available.'}</small>
                    </span>
                  </label>
                ))
              ) : (
                <div className="mcp-access__message">
                  {normalizedToolQuery
                    ? 'No tools match this search.'
                    : 'Refresh this MCP server to discover its tools.'}
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="empty-state mcp-access__detail-empty">
            Select an MCP server to configure its tools.
          </div>
        )}
      </section>
    </div>
  )
}
