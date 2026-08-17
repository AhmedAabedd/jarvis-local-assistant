import { useQueries, useQueryClient } from '@tanstack/react-query'
import { Check, Plug, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import { useMemo, useState } from 'react'
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
      const matches = options
        .filter((tool) => tool.tool_name === rule)
        .map((tool) => tool.name)
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
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(new Set<number>())
  const [refreshErrors, setRefreshErrors] = useState(new Map<number, string>())
  const selectedByServer = useMemo(
    () => new Map(sources.map((source) => [Number(source.mcp_server_id), source])),
    [sources],
  )
  const selectedCount = selectedToolOptions(groups, sources).length

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
      setRefreshErrors((current) => new Map(current).set(
        serverId,
        cause instanceof Error ? cause.message : 'Could not refresh this MCP server.',
      ))
    } finally {
      setRefreshing((current) => {
        const next = new Set(current)
        next.delete(serverId)
        return next
      })
    }
  }

  const normalizedQuery = query.trim().toLocaleLowerCase()
  return (
    <div className="mcp-access">
      <div className="mcp-access__summary">
        <div className={`mcp-access__mode ${sources.length ? '' : 'is-active'}`}>
          <span className="mcp-access__mode-icon"><ShieldCheck size={17} /></span>
          <span>
            <strong>Prompt only</strong>
            <small>Model and instructions, with no external tools.</small>
          </span>
          {!sources.length && <Check size={16} />}
        </div>
        <div className={`mcp-access__mode ${sources.length ? 'is-active' : ''}`}>
          <span className="mcp-access__mode-icon"><Plug size={17} /></span>
          <span>
            <strong>MCP capabilities</strong>
            <small>{selectedCount} tool{selectedCount === 1 ? '' : 's'} from {sources.length} server{sources.length === 1 ? '' : 's'}.</small>
          </span>
          {sources.length > 0 && <Check size={16} />}
        </div>
      </div>

      <div className="mcp-access__toolbar">
        <label>
          <Search size={14} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search servers and tools…"
          />
        </label>
        {sources.length > 0 && (
          <button type="button" onClick={() => onChange([])}>Use prompt only</button>
        )}
      </div>

      {!servers.length ? (
        <div className="empty-state mcp-access__empty">
          No MCP servers are connected. This subagent will run with its model and prompt only.
        </div>
      ) : (
        <div className="mcp-access__servers">
          {groups.map((group) => {
            const serverId = Number(group.server.id)
            const source = selectedByServer.get(serverId)
            const selected = new Set(
              source?.enabled_tools === null
                ? group.tools.map((tool) => tool.name)
                : source?.enabled_tools || [],
            )
            const visibleTools = group.tools.filter(
              (tool) =>
                !normalizedQuery ||
                group.server.name.toLocaleLowerCase().includes(normalizedQuery) ||
                tool.name.toLocaleLowerCase().includes(normalizedQuery) ||
                (tool.description || '').toLocaleLowerCase().includes(normalizedQuery),
            )
            if (normalizedQuery && !visibleTools.length && !group.server.name.toLocaleLowerCase().includes(normalizedQuery)) return null
            return (
              <section className={`mcp-access__server ${source ? 'is-selected' : ''}`} key={serverId}>
                <header>
                  <label>
                    <input
                      type="checkbox"
                      checked={Boolean(source)}
                      onChange={(event) => replaceSource(serverId, event.target.checked ? null : undefined)}
                    />
                    <span>
                      <strong>{group.server.name}</strong>
                      <small>{source ? `${selected.size || group.tools.length} selected` : `${group.tools.length} available`} · {group.server.connection_status || 'untested'}</small>
                    </span>
                  </label>
                  <button
                    type="button"
                    title="Refresh discovered tools"
                    disabled={refreshing.has(serverId)}
                    onClick={() => refresh(serverId)}
                  >
                    <RefreshCw size={13} className={refreshing.has(serverId) ? 'spin' : ''} />
                  </button>
                </header>
                {group.loading ? (
                  <div className="mcp-access__message">Loading discovered tools…</div>
                ) : group.error || refreshErrors.get(serverId) ? (
                  <div className="mcp-access__message is-error">
                    {group.error || refreshErrors.get(serverId)}
                  </div>
                ) : visibleTools.length ? (
                  <div className="mcp-access__tools">
                    {visibleTools.map((tool) => (
                      <label key={tool.name}>
                        <input
                          type="checkbox"
                          checked={selected.has(tool.name)}
                          onChange={(event) => toggleTool(group, tool.name, event.target.checked)}
                        />
                        <span>
                          <strong>{readable(tool.name)}</strong>
                          <small>{tool.description || 'No description available.'}</small>
                        </span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <div className="mcp-access__message">Test this server to discover its tools.</div>
                )}
              </section>
            )
          })}
        </div>
      )}
    </div>
  )
}
