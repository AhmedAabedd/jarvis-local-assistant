import { Plus, Trash2 } from 'lucide-react'
import { Button } from '../../components/ui/Button'

export interface Entry {
  key: string
  value: string
}
export function KeyValueEditor({
  title,
  entries,
  onChange,
  secret = true,
}: {
  title: string
  entries: Entry[]
  onChange: (entries: Entry[]) => void
  secret?: boolean
}) {
  return (
    <div className="key-value-editor">
      <div className="key-value-editor__title">
        <strong>{title}</strong>
        <Button
          type="button"
          icon={<Plus size={13} />}
          onClick={() => onChange([...entries, { key: '', value: '' }])}
        >
          Add
        </Button>
      </div>
      {!entries.length && <span className="field__hint">No values configured.</span>}
      {entries.map((entry, index) => (
        <div className="key-value-row" key={index}>
          <input
            aria-label={`${title} name`}
            placeholder="Name"
            value={entry.key}
            onChange={(e) =>
              onChange(entries.map((v, i) => (i === index ? { ...v, key: e.target.value } : v)))
            }
          />
          <input
            aria-label={`${title} value`}
            type={secret ? 'password' : 'text'}
            placeholder="Value"
            value={entry.value}
            onChange={(e) =>
              onChange(entries.map((v, i) => (i === index ? { ...v, value: e.target.value } : v)))
            }
          />
          <button
            type="button"
            className="icon-button"
            onClick={() => onChange(entries.filter((_, i) => i !== index))}
            aria-label="Remove"
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}

export function entriesObject(entries: Entry[], label: string, preservedKeys = new Set<string>()) {
  const result: Record<string, string> = {}
  for (const entry of entries) {
    const key = entry.key.trim()
    if (!key && !entry.value) continue
    if (!key) throw new Error(`${label} name is required.`)
    if (!entry.value && !preservedKeys.has(key))
      throw new Error(`${label} value is required for “${key}”.`)
    if (Object.hasOwn(result, key)) throw new Error(`${label} “${key}” is duplicated.`)
    result[key] = entry.value
  }
  return result
}
