import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, MessageCircle, Pencil, Plus, RefreshCcw, ShieldCheck, Trash2 } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { MetaWhatsAppConnection, MetaWhatsAppDefinition } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { Modal } from '../../components/ui/Modal'
import { Status } from '../../components/ui/Status'
import { Switch } from '../../components/ui/Switch'
import { keys } from '../../hooks/useStudioData'

export function MetaWhatsAppPage() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: keys.metaWhatsAppConnections,
    queryFn: api.meta.whatsapp.connections,
  })
  const definitionQuery = useQuery({
    queryKey: keys.metaWhatsAppDefinition,
    queryFn: api.meta.whatsapp.definition,
  })
  const [editor, setEditor] = useState<MetaWhatsAppConnection | 'new' | null>(null)
  const [removeTarget, setRemoveTarget] = useState<MetaWhatsAppConnection | null>(null)
  const [feedback, setFeedback] = useState('')
  const refresh = () => queryClient.invalidateQueries({ queryKey: keys.metaWhatsAppConnections })
  const create = useMutation({
    mutationFn: api.meta.whatsapp.create,
    onSuccess: async () => {
      await refresh()
      setEditor(null)
      setFeedback('WhatsApp Business agent connection saved.')
    },
  })
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: object }) =>
      api.meta.whatsapp.update(id, body),
    onSuccess: async () => {
      await refresh()
      setEditor(null)
      setFeedback('WhatsApp Business agent connection updated.')
    },
  })
  const remove = useMutation({
    mutationFn: api.meta.whatsapp.remove,
    onSuccess: async () => {
      await refresh()
      setRemoveTarget(null)
    },
  })
  const test = useMutation({
    mutationFn: api.meta.whatsapp.test,
    onSuccess: async () => {
      await refresh()
      setFeedback('WhatsApp Business API connection succeeded.')
    },
  })
  if (query.isLoading || definitionQuery.isLoading || !query.data || !definitionQuery.data)
    return <Loading />
  const definition = definitionQuery.data
  const error = [
    query.error,
    definitionQuery.error,
    create.error,
    update.error,
    remove.error,
    test.error,
  ].find(Boolean)
  return (
    <div className="stack">
      <section className="meta-platform-intro">
        <div className="meta-platform-intro__icon">
          <MessageCircle size={22} />
        </div>
        <div>
          <h2>WhatsApp Business agent</h2>
          <p>
            Read business inbox messages and let the WhatsApp specialist reply or send attachments
            through the official Cloud API.
          </p>
          <small>
            Separate from the paired WhatsApp channel. Free-form sends require an inbound contact
            and an open 24-hour service window.
          </small>
        </div>
        <Button variant="primary" icon={<Plus size={14} />} onClick={() => setEditor('new')}>
          Add connection
        </Button>
      </section>
      {query.data.length === 0 ? (
        <Card>
          <div className="empty-state">
            No WhatsApp Business agent connection exists yet. Add a sender number from your Meta
            business account; this does not pair a phone with Mounir.
          </div>
        </Card>
      ) : (
        query.data.map((connection) => {
          const callback = `${location.origin}${connection.webhook_path}`
          return (
            <Card
              key={connection.id}
              title={connection.name}
              description={
                connection.verified_name ||
                connection.display_phone_number ||
                `Business number ID ${connection.phone_number_id}`
              }
              action={<Status value={connection.connection_status} />}
            >
              <div className="card__body stack">
                <div className="meta-connection-row">
                  <span>
                    <strong>Enable for the WhatsApp agent</strong>
                    <small>This setting does not enable or disable the private chat channel.</small>
                  </span>
                  <Switch
                    checked={connection.enabled}
                    disabled={update.isPending}
                    label={`Enable ${connection.name}`}
                    onChange={(enabled) =>
                      update.mutate({ id: connection.id, body: { enabled } })
                    }
                  />
                </div>
                <div className="meta-callback">
                  <span>
                    <strong>Business inbox webhook</strong>
                    <small>Register this callback for incoming messages and delivery statuses.</small>
                  </span>
                  <code>{callback}</code>
                  <Button
                    icon={<Copy size={13} />}
                    onClick={() => navigator.clipboard.writeText(callback)}
                  >
                    Copy
                  </Button>
                </div>
                <div className="meta-callback">
                  <span>
                    <strong>Verify token</strong>
                    <small>Use this value when Meta verifies the business webhook.</small>
                  </span>
                  <code>{connection.verify_token}</code>
                  <Button
                    icon={<Copy size={13} />}
                    onClick={() => navigator.clipboard.writeText(connection.verify_token)}
                  >
                    Copy
                  </Button>
                </div>
                <div className="meta-accounts">
                  <div className="meta-accounts__heading">
                    <strong>Official capabilities and permissions</strong>
                    <small>
                      Token permissions are verified by Test connection; capability choices are
                      enforced by the agent.
                    </small>
                  </div>
                  {definition.permissions.map((permission) => {
                    const granted = connection.granted_permissions.includes(permission.id)
                    const status = !connection.permissions_checked_at
                      ? 'not checked'
                      : granted
                        ? 'verified'
                        : permission.required
                          ? 'missing'
                          : 'not granted'
                    return (
                      <div className="meta-account" key={permission.id}>
                        <span>
                          <strong>
                            {permission.label}
                            {permission.required ? ' · required' : ' · optional'}
                          </strong>
                          <small>
                            {permission.id} · {status}
                          </small>
                        </span>
                        {granted && <ShieldCheck size={18} />}
                      </div>
                    )
                  })}
                  {definition.capabilities.map((capability) => {
                    const selected = connection.requested_capabilities.includes(capability.id)
                    return (
                      <div className="meta-account" key={capability.id}>
                        <span>
                          <strong>
                            {capability.label}
                            {capability.available === false
                              ? ' · not exposed yet'
                              : selected
                                ? ' · enabled'
                                : ' · disabled'}
                          </strong>
                          <small>{capability.description}</small>
                        </span>
                        {selected && <ShieldCheck size={18} />}
                      </div>
                    )
                  })}
                </div>
                {connection.last_error && <Feedback message={connection.last_error} />}
                <div className="meta-actions">
                  <Button
                    icon={<RefreshCcw size={14} />}
                    busy={test.isPending}
                    disabled={!connection.credentials_configured}
                    onClick={() => test.mutate(connection.id)}
                  >
                    Test connection
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
              </div>
            </Card>
          )
        })
      )}
      <Feedback
        message={error instanceof Error ? error.message : feedback}
        kind={feedback && !error ? 'success' : 'error'}
      />
      <WhatsAppBusinessEditor
        open={editor !== null}
        connection={editor === 'new' ? null : editor}
        definition={definition}
        busy={create.isPending || update.isPending}
        error={
          (create.error || update.error) instanceof Error
            ? String((create.error || update.error)?.message)
            : ''
        }
        onClose={() => setEditor(null)}
        onSubmit={(body) =>
          editor === 'new'
            ? create.mutate(body)
            : editor && update.mutate({ id: editor.id, body })
        }
      />
      <ConfirmDialog
        open={removeTarget !== null}
        danger
        busy={remove.isPending}
        title="Remove WhatsApp Business agent connection?"
        message="Its credentials and persisted business inbox messages will be deleted. The private WhatsApp channel is not affected."
        confirmLabel="Remove connection"
        onConfirm={() => removeTarget && remove.mutate(removeTarget.id)}
        onCancel={() => setRemoveTarget(null)}
      />
    </div>
  )
}

function WhatsAppBusinessEditor({
  open,
  connection,
  definition,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  open: boolean
  connection: MetaWhatsAppConnection | null
  definition: MetaWhatsAppDefinition
  busy: boolean
  error: string
  onClose: () => void
  onSubmit: (body: object) => void
}) {
  const [capabilities, setCapabilities] = useState<string[]>([])
  useEffect(() => {
    if (!open) return
    const available = definition.capabilities
      .filter((capability) => capability.available !== false)
      .map((capability) => capability.id)
    setCapabilities(connection?.requested_capabilities || available)
  }, [connection, definition, open])
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget).entries()) as Record<
      string,
      unknown
    >
    if (connection && !String(values.access_token || '').trim()) delete values.access_token
    if (connection && !String(values.app_secret || '').trim()) delete values.app_secret
    onSubmit({
      ...values,
      enabled: connection?.enabled ?? true,
      requested_capabilities: capabilities,
    })
  }
  return (
    <Modal
      open={open}
      wide
      title={`${connection ? 'Edit' : 'Add'} WhatsApp Business agent connection`}
      description="These credentials are dedicated to the business agent and are not copied from the private WhatsApp channel."
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="whatsapp-business-form" variant="primary" busy={busy}>
            Save connection
          </Button>
        </>
      }
    >
      <form id="whatsapp-business-form" className="form-grid" onSubmit={submit}>
        <Field label="Connection name" hint="A local label, for example Support number.">
          <input name="name" defaultValue={connection?.name || 'WhatsApp Business'} required />
        </Field>
        <Field label="Meta app ID" hint="Used to verify which permissions the token actually has.">
          <input name="app_id" defaultValue={connection?.app_id} required />
        </Field>
        <Field label="Phone number ID">
          <input name="phone_number_id" defaultValue={connection?.phone_number_id} required />
        </Field>
        <Field label="Business account ID">
          <input
            name="business_account_id"
            defaultValue={connection?.business_account_id}
            required
          />
        </Field>
        <Field
          label="Access token"
          hint={connection?.token_configured ? 'Saved — paste only to replace.' : undefined}
        >
          <input name="access_token" type="password" required={!connection?.token_configured} />
        </Field>
        <Field
          label="Meta app secret"
          hint={connection?.app_secret_configured ? 'Saved — paste only to replace.' : undefined}
        >
          <input name="app_secret" type="password" required={!connection?.app_secret_configured} />
        </Field>
        <Field label="Graph API version">
          <input
            name="api_version"
            defaultValue={connection?.api_version || definition.default_api_version}
            required
          />
        </Field>
        <fieldset className="meta-capabilities field--full">
          <legend>Official capabilities and permissions</legend>
          <p>
            WhatsApp permissions belong to the access token. Test connection verifies them;
            capability choices control which operations the agent may call.
          </p>
          <div className="meta-capability-grid">
            {definition.permissions.map((permission) => (
              <label key={permission.id} className={permission.required ? 'is-selected' : ''}>
                <input type="checkbox" checked={permission.required} disabled readOnly />
                <span>
                  <strong>
                    {permission.label} · {permission.required ? 'required' : 'optional'}
                  </strong>
                  <small>
                    {permission.description} Permission: {permission.id}
                  </small>
                </span>
              </label>
            ))}
            {definition.capabilities.map((capability) => {
              const unavailable = capability.available === false
              const selected =
                !unavailable &&
                (capability.required || capabilities.includes(capability.id))
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
                    <small>
                      {capability.description} Permissions: {capability.permissions.join(', ')}
                    </small>
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
