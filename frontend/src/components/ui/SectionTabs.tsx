import type { ReactNode } from 'react'

export interface SectionTabOption {
  id: string
  label: string
  icon: ReactNode
  count?: number
}

export function SectionTabs({
  value,
  options,
  label,
  className = '',
  onChange,
}: {
  value: string
  options: SectionTabOption[]
  label: string
  className?: string
  onChange: (value: string) => void
}) {
  return (
    <nav className={`section-tabs ${className}`} aria-label={label} role="tablist">
      {options.map((option) => (
        <button
          type="button"
          role="tab"
          className={value === option.id ? 'is-active' : ''}
          aria-selected={value === option.id}
          key={option.id}
          onClick={() => onChange(option.id)}
        >
          {option.icon}
          {option.label}
          {option.count !== undefined && (
            <span className="section-tabs__count">{option.count}</span>
          )}
        </button>
      ))}
    </nav>
  )
}
