import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, KeyRound, Link2, PlugZap, Unlink } from 'lucide-react'
import { useEffect, useState } from 'react'
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

export function TelegramPage() {
  const client = useQueryClient(),
    profile = useProfile().data,
    query = useQuery({
      queryKey: keys.telegram,
      queryFn: api.telegram.get,
      refetchInterval: (data) =>
        ['connecting', 'waiting_pairing'].includes(data.state.data?.connection_status || '')
          ? 1500
          : false,
    })
  const [token, setToken] = useState(''),
    [editingToken, setEditingToken] = useState(false),
    [pair, setPair] = useState<{ code: string; command?: string }>(),
    [confirm, setConfirm] = useState<'token' | 'pair' | null>(null),
    [success, setSuccess] = useState('')
  const state = query.data
  const refresh = async () => client.invalidateQueries({ queryKey: keys.telegram })
  const update = useMutation({ mutationFn: api.telegram.update, onSuccess: async () => refresh() })
  const test = useMutation({
    mutationFn: api.telegram.test,
    onSuccess: async () => {
      await refresh()
      setSuccess('Telegram connection succeeded.')
    },
  })
  const pairing = useMutation({
    mutationFn: api.telegram.pairingCode,
    onSuccess: (data) => setPair(data),
  })
  const removeToken = useMutation({
    mutationFn: api.telegram.removeToken,
    onSuccess: async () => {
      await refresh()
      setConfirm(null)
      setPair(undefined)
    },
  })
  const disconnect = useMutation({
    mutationFn: api.telegram.disconnect,
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
        <PageHeader title="Telegram" description="Connect to your assistant from Telegram" />
        <Loading />
      </>
    )
  const bot = state.bot_username ? `@${state.bot_username}` : 'Telegram bot',
    assistant = profile?.assistant_name || 'Mounir'
  const error = [
    query.error,
    update.error,
    test.error,
    pairing.error,
    removeToken.error,
    disconnect.error,
  ].find(Boolean)
  return (
    <>
      <PageHeader title="Telegram" description="Connect to your assistant from Telegram" />
      <div className="page-content stack">
        <ConnectionHero
          image="/images/telegram.svg"
          title={bot}
          detail={
            state.last_error ||
            (state.paired
              ? `Paired with ${state.chat_name || state.chat_username || 'your account'}.`
              : 'Configure a bot and pair your private chat.')
          }
          status={state.connection_status}
        />
        <EnableCard
          name="Telegram"
          assistant={assistant}
          checked={state.enabled}
          busy={update.isPending}
          onChange={(enabled) => update.mutate({ enabled })}
        />
        <Card
          title="Bot connection"
          description="Create a bot with BotFather and save its token. Saved tokens are never returned by the API."
        >
          <div className="card__body stack">
            <Feedback
              message={state.token_configured ? 'Bot token saved.' : 'No bot token saved.'}
              kind={state.token_configured ? 'success' : 'info'}
            />
            {editingToken || !state.token_configured ? (
              <Field label="Bot token">
                <input
                  type="password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="Paste token from BotFather"
                />
              </Field>
            ) : null}
            <SettingsActions>
              {(editingToken || !state.token_configured) && (
                <Button
                  variant="primary"
                  icon={<KeyRound size={14} />}
                  disabled={!token.trim()}
                  busy={update.isPending}
                  onClick={() =>
                    update.mutate(
                      { bot_token: token },
                      {
                        onSuccess: () => {
                          setToken('')
                          setEditingToken(false)
                        },
                      },
                    )
                  }
                >
                  {state.token_configured ? 'Replace token' : 'Save token'}
                </Button>
              )}
              {state.token_configured && (
                <>
                  <Button onClick={() => setEditingToken(!editingToken)}>
                    {editingToken ? 'Cancel' : 'Replace token'}
                  </Button>
                  <Button
                    icon={<PlugZap size={14} />}
                    busy={test.isPending}
                    onClick={() => test.mutate()}
                  >
                    Test connection
                  </Button>
                  <Button variant="danger" onClick={() => setConfirm('token')}>
                    Remove token
                  </Button>
                </>
              )}
            </SettingsActions>
          </div>
        </Card>
        <Card
          title="Private account pairing"
          description="Only the paired Telegram chat can send requests to this assistant."
        >
          <div className="card__body stack">
            {state.paired ? (
              <div className="availability">
                <span>
                  <strong>{state.chat_name || 'Telegram account'}</strong>
                  <small>{state.chat_username ? `@${state.chat_username}` : 'Connected'}</small>
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
                <span>Send this one-use command to {bot}</span>
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
                  disabled={!state.enabled || !state.token_configured}
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
          message={error instanceof Error ? error.message : success}
          kind={success ? 'success' : 'error'}
        />
      </div>
      <ConfirmDialog
        open={confirm === 'token'}
        danger
        title="Remove bot token?"
        message="Telegram will stop and its saved token will be deleted."
        confirmLabel="Remove token"
        onConfirm={() => removeToken.mutate()}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === 'pair'}
        danger
        title="Disconnect Telegram account?"
        message="The paired account will no longer be authorized to message this assistant."
        confirmLabel="Disconnect"
        onConfirm={() => disconnect.mutate()}
        onCancel={() => setConfirm(null)}
      />
    </>
  )
}
