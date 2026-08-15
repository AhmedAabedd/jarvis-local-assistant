import { ShieldAlert } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Button } from '../../components/ui/Button'

interface Props {
  prompt: string
  onAllow: () => void
  onDeny: () => void
}

export function ToolApprovalPanel({ prompt, onAllow, onDeny }: Props) {
  const denyButton = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    denyButton.current?.focus()
  }, [prompt])

  return (
    <section
      className="tool-approval"
      role="alertdialog"
      aria-labelledby="tool-approval-title"
      aria-describedby="tool-approval-description"
    >
      <span className="tool-approval__icon" aria-hidden="true">
        <ShieldAlert size={18} />
      </span>
      <div className="tool-approval__content">
        <strong id="tool-approval-title">Approval required</strong>
        <p id="tool-approval-description">{prompt}</p>
      </div>
      <div className="tool-approval__actions">
        <Button ref={denyButton} type="button" onClick={onDeny}>
          Deny
        </Button>
        <Button type="button" variant="primary" onClick={onAllow}>
          Allow
        </Button>
      </div>
    </section>
  )
}
