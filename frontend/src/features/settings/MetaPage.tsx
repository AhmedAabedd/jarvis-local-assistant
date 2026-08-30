import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AtSign,
  Copy,
  Facebook,
  Instagram,
  MessageCircle,
  Pencil,
  Plus,
  RefreshCcw,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import type { MetaConnection, MetaPlatformDefinition, MetaPlatformId } from '../../api/types'
import { api } from '../../api/client'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { Modal } from '../../components/ui/Modal'
import { SectionTabs } from '../../components/ui/SectionTabs'
import { Status } from '../../components/ui/Status'
import { Switch } from '../../components/ui/Switch'
import { keys } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'
import { MetaWhatsAppPage } from './MetaWhatsAppPage'

type MetaTab = MetaPlatformId | 'whatsapp'

const metaTabs: Array<{ id: MetaTab; label: string; icon: typeof Facebook }> = [
  { id: 'facebook', label: 'Facebook', icon: Facebook },
  { id: 'messenger', label: 'Messenger', icon: MessageCircle },
  { id: 'instagram', label: 'Instagram', icon: Instagram },
  { id: 'threads', label: 'Threads', icon: AtSign },
  { id: 'whatsapp', label: 'WhatsApp', icon: MessageCircle },
]

function isMetaTab(value?: string): value is MetaTab {
  return metaTabs.some((tab) => tab.id === value)
}

export function MetaPage() {
  const { platform } = useParams(),
    navigate = useNavigate(),
    [search] = useSearchParams(),
    active: MetaTab = isMetaTab(platform) ? platform : 'facebook'
  const platforms = useQuery({ queryKey: keys.metaPlatforms, queryFn: api.meta.platforms })
  const connections = useQuery({
    queryKey: keys.metaConnections,
    queryFn: () => api.meta.connections(),
  })
  const whatsappConnections = useQuery({
    queryKey: keys.metaWhatsAppConnections,
    queryFn: api.meta.whatsapp.connections,
  })
  useEffect(() => {
    if (!isMetaTab(platform)) navigate('/admin/meta/facebook', { replace: true })
  }, [navigate, platform])
  useEffect(() => {
    if (search.get('oauth')) connections.refetch()
  }, [search])
  const options = metaTabs.map(({ icon: Icon, ...tab }) => ({
    ...tab,
    icon: <Icon size={15} />,
    count:
      tab.id === 'whatsapp'
        ? whatsappConnections.data?.length
        : connections.data?.filter((item) => item.platform === tab.id).length,
  }))
  const definition = platforms.data?.find((item) => item.id === active)
  return (
    <>
      <PageHeader
        title="Meta"
        description="Official connections for Meta's social and messaging apps"
      />
      <div className="page-content meta-workspace">
        <SectionTabs
          value={active}
          options={options}
          label="Meta apps"
          className="meta-tabs"
          onChange={(value) => navigate(`/admin/meta/${value}`)}
        />
        <div className="meta-tab-content">
          {active === 'whatsapp' ? (
            <MetaWhatsAppPage />
          ) : platforms.isLoading || connections.isLoading || !definition ? (
            <Loading />
          ) : (
            <MetaConnectionPage
              definition={definition}
              connections={(connections.data || []).filter((item) => item.platform === active)}
              oauthResult={search.get('oauth') || ''}
            />
          )}
        </div>
      </div>
    </>
  )
}

function MetaConnectionPage({
  definition,
  connections,
  oauthResult,
}: {
  definition: MetaPlatformDefinition
  connections: MetaConnection[]
  oauthResult: string
}) {
  const queryClient = useQueryClient()
  const [editor, setEditor] = useState<MetaConnection | 'new' | null>(null)
  const [removeTarget, setRemoveTarget] = useState<MetaConnection | null>(null)
  const [feedback, setFeedback] = useState('')
  const refresh = () => queryClient.invalidateQueries({ queryKey: keys.metaConnections })
  const create = useMutation({
    mutationFn: api.meta.create,
    onSuccess: async () => {
      await refresh()
      setEditor(null)
      setFeedback(`${definition.label} connection saved.`)
    },
  })
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: object }) => api.meta.update(id, body),
    onSuccess: async () => {
      await refresh()
      setEditor(null)
      setFeedback(`${definition.label} connection updated.`)
    },
  })
  const remove = useMutation({
    mutationFn: api.meta.remove,
    onSuccess: async () => {
      await refresh()
      setRemoveTarget(null)
    },
  })
  const oauth = useMutation({
    mutationFn: api.meta.startOauth,
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
  })
  const test = useMutation({
    mutationFn: api.meta.test,
    onSuccess: async () => {
      await refresh()
      setFeedback('Connection tested and account list refreshed.')
    },
  })
  const accountUpdate = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.meta.updateAccount(id, enabled),
    onSuccess: refresh,
  })
  const enabledUpdate = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.meta.update(id, { enabled }),
    onSuccess: refresh,
  })
  const error = [
    create.error,
    update.error,
    remove.error,
    oauth.error,
    test.error,
    accountUpdate.error,
    enabledUpdate.error,
  ].find(Boolean)
  return (
    <div className="stack">
      <section className="meta-platform-intro">
        <div className="meta-platform-intro__icon">
          <ShieldCheck size={22} />
        </div>
        <div>
          <h2>{definition.label}</h2>
          <p>{definition.description}</p>
          <small>Not included: {definition.excluded.join(', ')}.</small>
        </div>
        <Button variant="primary" icon={<Plus size={14} />} onClick={() => setEditor('new')}>
          Add connection
        </Button>
      </section>
      {oauthResult === 'connected' && (
        <Feedback message="Meta account connected successfully." kind="success" />
      )}
      {oauthResult === 'error' && (
        <Feedback message="Meta sign-in did not complete. Open the connection for details." />
      )}
      {connections.length === 0 ? (
        <Card>
          <div className="empty-state">
            No {definition.label} app is connected yet. Add your Meta developer app to begin.
          </div>
        </Card>
      ) : (
        <div className="stack">
          {connections.map((connection) => {
            const callback =
              connection.redirect_uri ||
              `${location.origin}/api/meta/connections/${connection.id}/oauth/callback`
            return (
              <Card
                key={connection.id}
                title={connection.name}
                description={`${definition.account_kind} · ${connection.auth_strategy.replaceAll('_', ' ')}`}
                action={<Status value={connection.connection_status} />}
              >
                <div className="card__body stack">
                  <div className="meta-connection-row">
                    <span>
                      <strong>Enable connection</strong>
                      <small>
                        Only enabled accounts are available to the {definition.label} agent.
                      </small>
                    </span>
                    <Switch
                      checked={connection.enabled}
                      disabled={enabledUpdate.isPending}
                      label={`Enable ${connection.name}`}
                      onChange={(enabled) => enabledUpdate.mutate({ id: connection.id, enabled })}
                    />
                  </div>
                  <div className="meta-callback">
                    <span>
                      <strong>OAuth callback URL</strong>
                      <small>Register this exact URL in the Meta developer app.</small>
                    </span>
                    <code>{callback}</code>
                    <Button
                      icon={<Copy size={13} />}
                      onClick={() => navigator.clipboard.writeText(callback)}
                    >
                      Copy
                    </Button>
                  </div>
                  {connection.last_error && <Feedback message={connection.last_error} />}
                  <div className="meta-actions">
                    <Button
                      variant="primary"
                      icon={<ShieldCheck size={14} />}
                      busy={oauth.isPending}
                      disabled={!connection.enabled}
                      onClick={() => oauth.mutate(connection.id)}
                    >
                      {connection.token_configured ? 'Reconnect account' : 'Connect with OAuth'}
                    </Button>
                    <Button
                      icon={<RefreshCcw size={14} />}
                      busy={test.isPending}
                      disabled={!connection.token_configured}
                      onClick={() => test.mutate(connection.id)}
                    >
                      Test and refresh
                    </Button>
                    <Button icon={<Pencil size={14} />} onClick={() => setEditor(connection)}>
                      Edit
                    </Button>
                    <Button
                      variant="danger"
                      icon={<Trash2 size={14} />}
                      onClick={() => setRemoveTarget(connection)}
                    >
                      Remove
                    </Button>
                  </div>
                  <div className="meta-accounts">
                    <div className="meta-accounts__heading">
                      <strong>Discovered accounts</strong>
                      <small>{connection.accounts.length} found through the official API</small>
                    </div>
                    {connection.accounts.length ? (
                      connection.accounts.map((account) => (
                        <div className="meta-account" key={account.id}>
                          <span>
                            <strong>
                              {account.name || account.username || account.external_id}
                            </strong>
                            <small>
                              {account.username ? `@${account.username} · ` : ''}
                              {account.account_type.replaceAll('_', ' ')}
                            </small>
                          </span>
                          <Switch
                            checked={account.enabled}
                            disabled={accountUpdate.isPending}
                            label={`Enable ${account.name || account.external_id}`}
                            onChange={(enabled) =>
                              accountUpdate.mutate({ id: account.id, enabled })
                            }
                          />
                        </div>
                      ))
                    ) : (
                      <p className="meta-accounts__empty">
                        Connect with OAuth, then Mounir will discover eligible accounts here.
                      </p>
                    )}
                  </div>
                </div>
              </Card>
            )
          })}
        </div>
      )}
      <Feedback
        message={error instanceof Error ? error.message : feedback}
        kind={feedback && !error ? 'success' : 'error'}
      />
      <MetaConnectionEditor
        open={editor !== null}
        definition={definition}
        connection={editor === 'new' ? null : editor}
        busy={create.isPending || update.isPending}
        error={
          (create.error || update.error) instanceof Error
            ? String((create.error || update.error)?.message)
            : ''
        }
        onClose={() => setEditor(null)}
        onSubmit={(body) =>
          editor === 'new' ? create.mutate(body) : editor && update.mutate({ id: editor.id, body })
        }
      />
      <ConfirmDialog
        open={removeTarget !== null}
        danger
        busy={remove.isPending}
        title={`Remove ${definition.label} connection?`}
        message="The saved OAuth token and discovered accounts will be deleted from this Mounir installation."
        confirmLabel="Remove connection"
        onConfirm={() => removeTarget && remove.mutate(removeTarget.id)}
        onCancel={() => setRemoveTarget(null)}
      />
    </div>
  )
}

function MetaConnectionEditor({
  open,
  definition,
  connection,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  open: boolean
  definition: MetaPlatformDefinition
  connection: MetaConnection | null
  busy: boolean
  error: string
  onClose: () => void
  onSubmit: (body: object) => void
}) {
  const [authStrategy, setAuthStrategy] = useState('')
  const [capabilities, setCapabilities] = useState<string[]>([])
  useEffect(() => {
    if (!open) return
    setAuthStrategy(connection?.auth_strategy || definition.auth_strategies[0]?.id || '')
    const available = new Set(
      definition.capabilities
        .filter((capability) => capability.available !== false)
        .map((capability) => capability.id),
    )
    setCapabilities(
      (connection?.requested_capabilities || []).filter((capability) => available.has(capability)),
    )
  }, [connection, definition, open])
  const visibleCapabilities = useMemo(
    () =>
      definition.capabilities.filter(
        (capability) =>
          !capability.scopes_by_auth || Boolean(capability.scopes_by_auth[authStrategy]),
      ),
    [authStrategy, definition],
  )
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget).entries()) as Record<
      string,
      unknown
    >
    if (connection && !String(values.app_secret || '').trim()) delete values.app_secret
    onSubmit({
      ...values,
      platform: definition.id,
      auth_strategy: authStrategy,
      requested_capabilities: capabilities,
      enabled: connection?.enabled ?? true,
    })
  }
  return (
    <Modal
      open={open}
      wide
      title={`${connection ? 'Edit' : 'Add'} ${definition.label} connection`}
      description="Use credentials from your own Meta developer app. Secrets stay on this installation."
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="meta-connection-form" variant="primary" busy={busy}>
            Save connection
          </Button>
        </>
      }
    >
      <form id="meta-connection-form" className="form-grid" onSubmit={submit}>
        <Field label="Connection name">
          <input
            name="name"
            defaultValue={connection?.name || `${definition.label} app`}
            required
          />
        </Field>
        <Field label="Sign-in method">
          <select value={authStrategy} onChange={(event) => setAuthStrategy(event.target.value)}>
            {definition.auth_strategies.map((strategy) => (
              <option key={strategy.id} value={strategy.id}>
                {strategy.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Meta app ID">
          <input name="app_id" defaultValue={connection?.app_id} required />
        </Field>
        <Field
          label="Meta app secret"
          hint={connection?.app_secret_configured ? 'Saved — paste only to replace.' : undefined}
        >
          <input name="app_secret" type="password" required={!connection?.app_secret_configured} />
        </Field>
        <Field label="API version">
          <input
            name="api_version"
            defaultValue={connection?.api_version || definition.default_api_version}
            required
          />
        </Field>
        <Field
          label="Custom OAuth callback"
          hint="Optional. Leave empty to use this Mounir server."
        >
          <input
            name="redirect_uri"
            type="url"
            defaultValue={connection?.redirect_uri}
            placeholder="https://mounir.example/api/meta/.../callback"
          />
        </Field>
        <fieldset className="meta-capabilities field--full">
          <legend>Official capabilities and permissions</legend>
          <p>
            Select only what this installation needs. Meta may require app review for advanced
            access.
          </p>
          <div className="meta-capability-grid">
            {visibleCapabilities.map((capability) => {
              const unavailable = capability.available === false
              const selected =
                !unavailable && (capability.required || capabilities.includes(capability.id))
              return (
                <label key={capability.id} className={selected ? 'is-selected' : ''}>
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={capability.required || unavailable}
                    onChange={(event) =>
                      setCapabilities((current) =>
                        event.target.checked
                          ? [...new Set([...current, capability.id])]
                          : current.filter((item) => item !== capability.id),
                      )
                    }
                  />
                  <span>
                    <strong>
                      {capability.label}
                      {capability.required
                        ? ' · required'
                        : unavailable
                          ? ' · not exposed yet'
                          : ''}
                    </strong>
                    <small>{capability.description}</small>
                  </span>
                </label>
              )
            })}
          </div>
        </fieldset>
        <div className="field--full">
          <Feedback message={error} />
        </div>
      </form>
    </Modal>
  )
}
