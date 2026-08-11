import { Button } from './Button'
import { Feedback } from './Feedback'
import { Modal } from './Modal'

interface Props {
  open: boolean
  title?: string
  message: string
  confirmLabel?: string
  danger?: boolean
  busy?: boolean
  error?: string
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open,
  title = 'Are you sure?',
  message,
  confirmLabel = 'Continue',
  danger,
  busy,
  error,
  onConfirm,
  onCancel,
}: Props) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={onCancel}
      footer={
        <>
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} busy={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="confirm-copy">{message}</p>
      <Feedback message={error} />
    </Modal>
  )
}
