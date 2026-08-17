import type { ToolInfo } from '../../api/types'
import { readable } from './helpers'

export function ToolChoices({
  tools,
  selected,
  onChange,
  readOnly = false,
  empty = 'Test the selected MCP server to discover its tools.',
}: {
  tools: ToolInfo[]
  selected?: Set<string>
  onChange?: (next: Set<string>) => void
  readOnly?: boolean
  empty?: string
}) {
  if (!tools.length)
    return (
      <div className="empty-state" style={{ minHeight: 90 }}>
        {empty}
      </div>
    )
  return (
    <div className="tool-list">
      {tools.map((tool) => {
        const content = (
          <>
            {!readOnly && (
              <input
                type="checkbox"
                checked={selected?.has(tool.name) || false}
                onChange={(e) => {
                  const next = new Set(selected || [])
                  e.target.checked ? next.add(tool.name) : next.delete(tool.name)
                  onChange?.(next)
                }}
              />
            )}
            <span>
              <strong>{tool.label || readable(tool.name)}</strong>
              <small>{tool.description || 'No description available.'}</small>
              {tool.server_name && <em className="tool-option__source">{tool.server_name}</em>}
            </span>
          </>
        )
        return readOnly ? (
          <div className="tool-option tool-option--readonly" key={tool.name}>
            {content}
          </div>
        ) : (
          <label className="tool-option" key={tool.name}>
            {content}
          </label>
        )
      })}
    </div>
  )
}
