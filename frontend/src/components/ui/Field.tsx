import type { ReactNode } from 'react'

interface Props {
  label: string
  hint?: string
  error?: string
  children: ReactNode
  full?: boolean
}

export function Field({ label, hint, error, children, full }: Props) {
  return (
    <label className={`field ${full ? 'field--full' : ''}`}>
      <span className="field__label">{label}</span>
      {hint && <span className="field__hint">{hint}</span>}
      {children}
      {error && <span className="field__error">{error}</span>}
    </label>
  )
}
