import type { ToolInfo } from '../../api/types'
import { readable } from './helpers'

export function ToolChoices({
  tools,
  selected,
  onChange,
  empty = 'Test the selected MCP server to discover its tools.',
}: {
  tools: ToolInfo[]
  selected: Set<string>
  onChange: (next: Set<string>) => void
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
      {tools.map((tool) => (
        <label className="tool-option" key={tool.name}>
          <input
            type="checkbox"
            checked={selected.has(tool.name)}
            onChange={(e) => {
              const next = new Set(selected)
              e.target.checked ? next.add(tool.name) : next.delete(tool.name)
              onChange(next)
            }}
          />
          <span>
            <strong>{readable(tool.name)}</strong>
            <small>{tool.description || 'No description available.'}</small>
          </span>
        </label>
      ))}
    </div>
  )
}
