import type { HTMLAttributes, ReactNode } from 'react'

interface Props extends HTMLAttributes<HTMLElement> {
  title?: string
  description?: string
  action?: ReactNode
}

export function Card({ title, description, action, children, className = '', ...props }: Props) {
  return (
    <section className={`card ${className}`} {...props}>
      {(title || description || action) && (
        <header className="card__header">
          <div>
            {title && <h3>{title}</h3>}
            {description && <p>{description}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  )
}
