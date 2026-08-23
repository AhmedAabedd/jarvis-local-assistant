import { BookOpen, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { SkillRecord } from '../../api/types'

export function SkillPicker({
  skills,
  selected,
  loading,
  error,
  onChange,
}: {
  skills: SkillRecord[]
  selected: Set<number>
  loading?: boolean
  error?: string
  onChange: (selected: Set<number>) => void
}) {
  const [search, setSearch] = useState('')
  const visible = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    return skills.filter(
      (skill) =>
        !query ||
        skill.name.toLocaleLowerCase().includes(query) ||
        skill.description.toLocaleLowerCase().includes(query),
    )
  }, [search, skills])

  if (loading) return <div className="guidance">Loading installed skills…</div>
  if (error) return <div className="guidance guidance--error">{error}</div>
  if (!skills.length) {
    return <div className="guidance">No skills are installed yet.</div>
  }

  return (
    <div className="skill-picker">
      <label className="resource-search skill-picker__search">
        <Search size={14} />
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search installed skills…"
          aria-label="Search installed skills"
        />
      </label>
      <div className="skill-picker__list">
        {visible.length ? (
          visible.map((skill) => {
            const id = Number(skill.id)
            const checked = selected.has(id)
            return (
              <label className={`skill-picker__item ${checked ? 'is-selected' : ''}`} key={id}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(event) => {
                    const next = new Set(selected)
                    if (event.target.checked) next.add(id)
                    else next.delete(id)
                    onChange(next)
                  }}
                />
                <BookOpen size={15} />
                <span>
                  <strong>{skill.name}</strong>
                  <small>{skill.description}</small>
                </span>
              </label>
            )
          })
        ) : (
          <div className="skill-picker__empty">No installed skills match “{search.trim()}”.</div>
        )}
      </div>
    </div>
  )
}
