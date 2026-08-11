export function Feedback({
  message,
  kind = 'error',
}: {
  message?: string
  kind?: 'error' | 'success' | 'info'
}) {
  if (!message) return null
  return (
    <div className={`feedback feedback--${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {message}
    </div>
  )
}
