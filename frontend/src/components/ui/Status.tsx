interface Props {
  value?: string
  label?: string
}

export function Status({ value = '', label }: Props) {
  const normalized = value.toLowerCase()
  const tone = ['connected', 'ready', 'ok', 'completed', 'success'].some((v) =>
    normalized.includes(v),
  )
    ? 'success'
    : ['error', 'failed', 'invalid'].some((v) => normalized.includes(v))
      ? 'danger'
      : ['connecting', 'running', 'waiting'].some((v) => normalized.includes(v))
        ? 'warning'
        : 'neutral'
  return (
    <span className={`status status--${tone}`}>
      <i />
      {label || value.replaceAll('_', ' ') || 'Unknown'}
    </span>
  )
}
