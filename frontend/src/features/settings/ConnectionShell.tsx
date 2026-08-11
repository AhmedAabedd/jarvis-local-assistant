import type { ReactNode } from 'react'
import { Card } from '../../components/ui/Card'
import { Status } from '../../components/ui/Status'
import { Switch } from '../../components/ui/Switch'

export function ConnectionHero({
  image,
  title,
  detail,
  status,
}: {
  image: string
  title: string
  detail: string
  status: string
}) {
  return (
    <section className="card connection-hero">
      <div className="connection-identity">
        <img src={image} alt="" />
        <div>
          <h3>{title}</h3>
          <p>{detail}</p>
        </div>
      </div>
      <Status value={status} />
    </section>
  )
}
export function EnableCard({
  name,
  assistant,
  checked,
  onChange,
  busy,
}: {
  name: string
  assistant: string
  checked: boolean
  onChange: (v: boolean) => void
  busy: boolean
}) {
  return (
    <Card>
      <div className="setting-row">
        <span>
          <strong>Enable {name}</strong>
          <small>Keep this channel available whenever {assistant} is running.</small>
        </span>
        <Switch checked={checked} onChange={onChange} disabled={busy} label={`Enable ${name}`} />
      </div>
    </Card>
  )
}
export function SettingsActions({ children }: { children: ReactNode }) {
  return (
    <div className="page-actions" style={{ justifyContent: 'flex-start' }}>
      {children}
    </div>
  )
}
