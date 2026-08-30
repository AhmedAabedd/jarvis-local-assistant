import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Link2, PlugZap, RefreshCcw, Save, Unlink } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { keys, useProfile } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'
import { ConnectionHero, EnableCard, SettingsActions } from './ConnectionShell'

export function WhatsAppPage() {
  const client = useQueryClient(),
    profile = useProfile().data,
    query = useQuery({ queryKey: keys.whatsapp, queryFn: api.whatsapp.get }),
    state = query.data
  const [pair, setPair] = useState<{ code: string; command?: string }>(),
    [confirm, setConfirm] = useState<'credentials' | 'pair' | null>(null),
    [success, setSuccess] = useState('')
  const refresh = async () => client.invalidateQueries({ queryKey: keys.whatsapp })
  const update = useMutation({ mutationFn: api.whatsapp.update, onSuccess: async () => refresh() })
  const test = useMutation({
    mutationFn: api.whatsapp.test,
    onSuccess: async () => {
      await refresh()
      setSuccess('WhatsApp connection succeeded.')
    },
  })
  const pairing = useMutation({ mutationFn: api.whatsapp.pairingCode, onSuccess: setPair })
  const remove = useMutation({
    mutationFn: api.whatsapp.removeCredentials,
    onSuccess: async () => {
      await refresh()
      setConfirm(null)
      setPair(undefined)
    },
  })
  const disconnect = useMutation({
    mutationFn: api.whatsapp.disconnect,
    onSuccess: async () => {
      await refresh()
      setConfirm(null)
    },
  })
  useEffect(() => {
    if (!pair || state?.paired) return
    const timer = window.setInterval(() => query.refetch(), 1500)
    return () => clearInterval(timer)
  }, [pair, state?.paired])
  if (query.isLoading || !state)
    return (
      <>
        <PageHeader title="WhatsApp channel" description="Chat privately with your assistant from a paired phone" />
        <Loading />
      </>
    )
  const assistant = profile?.assistant_name || 'Mounir',
    callbackUrl = `${location.origin}${state.webhook_path || '/api/whatsapp/webhook'}`
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget).entries())
    if (!String(values.access_token || '').trim()) delete values.access_token
    if (!String(values.app_secret || '').trim()) delete values.app_secret
    update.mutate(values, { onSuccess: () => setSuccess('WhatsApp connection saved.') })
  }
  const errors = [
    query.error,
    update.error,
    test.error,
    pairing.error,
    remove.error,
    disconnect.error,
  ].find(Boolean)
  return (
    <>
      <PageHeader
        title="WhatsApp channel"
        description="Chat privately with your assistant from a paired phone"
      />
      <div className="page-content stack">
        <ConnectionHero
          image="/images/whatsapp.svg"
          title={state.verified_name || state.display_phone_number || 'WhatsApp channel'}
          detail={
            state.last_error ||
            (state.paired
              ? `Paired with ${state.paired_name || state.paired_phone_hint || 'your phone'}.`
              : 'Configure Cloud API credentials, verify the webhook, then pair a phone.')
          }
          status={state.connection_status}
        />
        <EnableCard
          name="WhatsApp channel"
          assistant={assistant}
          checked={state.enabled}
          busy={update.isPending}
          onChange={(enabled) => update.mutate({ enabled })}
        />
        <Card
          title="Cloud API connection"
          description="Credentials are stored locally and never returned after saving."
        >
          <form className="card__body form-grid" onSubmit={submit}>
            <Field label="Phone number ID">
              <input name="phone_number_id" defaultValue={state.phone_number_id} required />
            </Field>
            <Field label="Business account ID">
              <input name="business_account_id" defaultValue={state.business_account_id} required />
            </Field>
            <Field
              label="Access token"
              hint={
                state.token_configured
                  ? 'Saved — paste only to replace.'
                  : 'Permanent system-user token.'
              }
            >
              <input type="password" name="access_token" />
            </Field>
            <Field
              label="App secret"
              hint={
                state.app_secret_configured
                  ? 'Saved — paste only to replace.'
                  : 'From Meta App Settings.'
              }
            >
              <input type="password" name="app_secret" />
            </Field>
            <Field label="API version">
              <input name="api_version" defaultValue={state.api_version || 'v25.0'} required />
            </Field>
            <Field label="Heartbeat template">
              <input
                name="heartbeat_template_name"
                defaultValue={state.heartbeat_template_name}
                placeholder="Optional approved template"
              />
            </Field>
            <Field label="Template language">
              <input
                name="heartbeat_template_language"
                defaultValue={state.heartbeat_template_language || 'en_US'}
              />
            </Field>
            <div className="form-footer">
              <Button variant="primary" icon={<Save size={14} />} busy={update.isPending}>
                Save connection
              </Button>
              <Button
                type="button"
                icon={<PlugZap size={14} />}
                disabled={!state.credentials_configured}
                busy={test.isPending}
                onClick={() => test.mutate()}
              >
                Test
              </Button>
              {state.credentials_configured && (
                <Button type="button" variant="danger" onClick={() => setConfirm('credentials')}>
                  Remove
                </Button>
              )}
            </div>
          </form>
        </Card>
        <Card
          title="Webhook"
          description="Add this callback URL and verification token in Meta. Incoming payloads are signature-verified."
        >
          <div className="card__body form-grid">
            <Field full label="Callback URL">
              <div style={{ display: 'flex', gap: 8 }}>
                <input className="mono" readOnly value={callbackUrl} />
                <Button
                  icon={<Copy size={13} />}
                  onClick={() => navigator.clipboard.writeText(callbackUrl)}
                >
                  Copy
                </Button>
              </div>
            </Field>
            <Field full label="Verify token">
              <div style={{ display: 'flex', gap: 8 }}>
                <input className="mono" readOnly value={state.verify_token || ''} />
                <Button
                  icon={<Copy size={13} />}
                  onClick={() => navigator.clipboard.writeText(state.verify_token || '')}
                >
                  Copy
                </Button>
                <Button
                  icon={<RefreshCcw size={13} />}
                  onClick={() => update.mutate({ regenerate_verify_token: true })}
                >
                  Regenerate
                </Button>
              </div>
            </Field>
            <div className="field--full">
              <Feedback
                message={
                  state.webhook_verified
                    ? 'Webhook verified by Meta.'
                    : 'Waiting for webhook verification.'
                }
                kind={state.webhook_verified ? 'success' : 'info'}
              />
            </div>
          </div>
        </Card>
        <Card
          title="Private phone pairing"
          description="Only the paired phone can submit assistant requests."
        >
          <div className="card__body stack">
            {state.paired ? (
              <div className="availability">
                <span>
                  <strong>{state.paired_name || state.paired_phone_hint || 'Paired phone'}</strong>
                  <small>Authorized WhatsApp phone</small>
                </span>
                <Button
                  variant="danger"
                  icon={<Unlink size={14} />}
                  onClick={() => setConfirm('pair')}
                >
                  Disconnect
                </Button>
              </div>
            ) : pair ? (
              <div className="pair-code">
                <span>Send this one-use command to the business number</span>
                <code>{pair.command || `/pair ${pair.code}`}</code>
                <Button
                  icon={<Copy size={13} />}
                  onClick={() =>
                    navigator.clipboard.writeText(pair.command || `/pair ${pair.code}`)
                  }
                >
                  Copy command
                </Button>
              </div>
            ) : (
              <SettingsActions>
                <Button
                  variant="primary"
                  icon={<Link2 size={14} />}
                  disabled={
                    !state.enabled || !state.credentials_configured || !state.webhook_verified
                  }
                  busy={pairing.isPending}
                  onClick={() => pairing.mutate()}
                >
                  Generate pairing code
                </Button>
              </SettingsActions>
            )}
          </div>
        </Card>
        <Feedback
          message={errors instanceof Error ? errors.message : success}
          kind={success ? 'success' : 'error'}
        />
      </div>
      <ConfirmDialog
        open={confirm === 'credentials'}
        danger
        title="Remove WhatsApp connection?"
        message="Saved credentials will be deleted and WhatsApp will be disabled."
        confirmLabel="Remove connection"
        onConfirm={() => remove.mutate()}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === 'pair'}
        danger
        title="Disconnect paired phone?"
        message="This phone will no longer be authorized to message the assistant."
        confirmLabel="Disconnect"
        onConfirm={() => disconnect.mutate()}
        onCancel={() => setConfirm(null)}
      />
    </>
  )
}
