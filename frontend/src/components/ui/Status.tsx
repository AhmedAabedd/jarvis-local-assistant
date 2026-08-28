interface Props {
  value?: string
  label?: string
}

export function statusTone(value = '') {
  const tokens = value
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean)
  const has = (...states: string[]) => states.some((state) => tokens.includes(state))
  const negatedPositive = has('not') && has('connected', 'ready', 'configured')
  return negatedPositive ||
    has('error', 'failed', 'invalid', 'disconnected', 'offline', 'unavailable')
    ? 'danger'
    : has('connecting', 'running', 'waiting', 'pending', 'stale', 'paused', 'alert', 'incomplete')
      ? 'warning'
      : has('configured', 'pairing')
        ? 'info'
        : has('connected', 'online', 'ready', 'ok', 'completed', 'success', 'active', 'enabled')
          ? 'success'
          : 'neutral'
}

export function Status({ value = '', label }: Props) {
  const tone = statusTone(value)
  return (
    <span className={`status status--${tone}`}>
      <i />
      {label || value.replaceAll('_', ' ') || 'Unknown'}
    </span>
  )
}

export function StatusDot({ value = '', label }: Props) {
  const display = label || value.replaceAll('_', ' ') || 'Unknown'
  return (
    <span
      className={`status-dot status-dot--${statusTone(value)}`}
      role="img"
      aria-label={display}
      title={display}
    />
  )
}
