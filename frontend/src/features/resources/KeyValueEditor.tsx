import { Plus, Trash2 } from 'lucide-react'
import { Button } from '../../components/ui/Button'

export interface Entry {
  id?: number
  key: string
  value: string
  configured?: boolean
  locked?: boolean
  preview?: string
}
export function KeyValueEditor({
  title,
  entries,
  onChange,
  secret = true,
  hint,
  namePlaceholder = 'Name',
  valuePlaceholder = 'Value',
}: {
  title: string
  entries: Entry[]
  onChange: (entries: Entry[]) => void
  secret?: boolean
  hint?: string
  namePlaceholder?: string
  valuePlaceholder?: string
}) {
  return (
    <div className="key-value-editor">
      <div className="key-value-editor__title">
        <span>
          <strong>{title}</strong>
          {hint && <small>{hint}</small>}
        </span>
        <Button
          type="button"
          icon={<Plus size={13} />}
          onClick={() => onChange([...entries, { key: '', value: '' }])}
        >
          Add
        </Button>
      </div>
      {!entries.length && <span className="field__hint">No values configured.</span>}
      {entries.map((entry, index) => {
        const locked = Boolean(entry.locked)
        return (
          <div className="key-value-row" key={index}>
            <input
              aria-label={`${title} name`}
              placeholder={namePlaceholder}
              value={entry.key}
              disabled={locked}
              onChange={(e) =>
                onChange(entries.map((v, i) => (i === index ? { ...v, key: e.target.value } : v)))
              }
            />
            <input
              aria-label={`${title} value`}
              type={secret && !locked ? 'password' : 'text'}
              placeholder={
                entry.configured && !entry.value ? 'Saved — enter to replace' : valuePlaceholder
              }
              value={locked ? entry.preview || '.....' : entry.value}
              disabled={locked}
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
        )
      })}
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
