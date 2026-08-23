import type { ReactNode } from 'react'

export function PageHeader({
  title,
  description,
  leading,
  actions,
}: {
  title: string
  description: string
  leading?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="page-header">
      <div className="page-header__heading">
        {leading && <div className="page-header__leading">{leading}</div>}
        <div className="page-header__copy">
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  )
}
