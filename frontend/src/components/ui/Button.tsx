import type { ButtonHTMLAttributes, ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'
interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  icon?: ReactNode
  busy?: boolean
}

export function Button({
  variant = 'secondary',
  icon,
  busy,
  children,
  className = '',
  disabled,
  ...props
}: Props) {
  return (
    <button
      className={`button button--${variant} ${className}`}
      disabled={disabled || busy}
      {...props}
    >
      {busy ? <span className="spinner" /> : icon}
      {children}
    </button>
  )
}
