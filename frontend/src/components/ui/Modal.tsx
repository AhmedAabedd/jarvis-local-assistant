import { X } from 'lucide-react'
import { useEffect, useId, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

interface Props {
  open: boolean
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  onClose: () => void
  wide?: boolean
  side?: boolean
  className?: string
  integrated?: boolean
  headingActions?: ReactNode
}

export function Modal({
  open,
  title,
  description,
  children,
  footer,
  onClose,
  wide,
  side,
  className,
  integrated,
  headingActions,
}: Props) {
  const titleId = useId()
  useEffect(() => {
    if (!open) return
    const closeOnEscape = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [open, onClose])
  if (!open) return null
  const panel = (
    <section
      className={`modal ${wide ? 'modal--wide' : ''} ${side ? 'modal--side' : ''} ${className || ''}`}
      role="dialog"
      aria-modal={side ? 'false' : 'true'}
      aria-labelledby={titleId}
    >
      {integrated ? (
        <div className="modal__scroll-frame">
          <div className="modal__body modal__body--integrated">
            <div className="modal__body-heading">
              <div>
                <h2 id={titleId}>{title}</h2>
                {description && <p>{description}</p>}
              </div>
              <div className="modal__body-heading-actions">
                {headingActions}
                <button
                  className="icon-button panel-close-button modal__integrated-close"
                  onClick={onClose}
                  aria-label="Close"
                >
                  <X size={14} />
                </button>
              </div>
            </div>
            {children}
          </div>
        </div>
      ) : (
        <>
          <header className="modal__header">
            <div>
              <h2 id={titleId}>{title}</h2>
              {description && <p>{description}</p>}
            </div>
            <button className="icon-button panel-close-button" onClick={onClose} aria-label="Close">
              <X size={14} />
            </button>
          </header>
          <div className="modal__body">{children}</div>
          {footer && <footer className="modal__footer">{footer}</footer>}
        </>
      )}
    </section>
  )
  return createPortal(
    side ? (
      panel
    ) : (
      <div
        className="modal-backdrop"
        role="presentation"
        onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      >
        {panel}
      </div>
    ),
    document.body,
  )
}
