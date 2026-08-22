import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Play, RefreshCw, Unplug } from 'lucide-react'
import { api } from '../../api/client'
import type { McpServer } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Loading } from '../../components/ui/Loading'
import { Status } from '../../components/ui/Status'
import { readable } from './helpers'

export function ServerTools({ server }: { server: McpServer }) {
  const client = useQueryClient()
  const key = ['server-tools', server.id]
  const tools = useQuery({ queryKey: key, queryFn: () => api.servers.tools(server.id) })
  const setup = useQuery({
    queryKey: ['server-setup', server.id],
    queryFn: () => api.servers.setup(server.id),
    refetchInterval: (query) => (query.state.data?.oauth.in_progress ? 1500 : false),
  })
  const refreshServerState = () => {
    client.invalidateQueries({ queryKey: ['servers'] })
    setup.refetch()
  }
  const test = useMutation({
    mutationFn: () => api.servers.test(server.id),
    onSuccess: (data) => {
      client.setQueryData(key, data)
      refreshServerState()
    },
  })
  const action = useMutation({
    mutationFn: (id: string) => api.servers.setupAction(server.id, id),
    onSuccess: () => {
      refreshServerState()
      client.invalidateQueries({ queryKey: key })
    },
  })

  const authorize = () => {
    const popup = window.open('', 'mounir-mcp-oauth', 'width=720,height=760')
    if (popup)
      popup.document.body.innerHTML =
        '<p style="font:14px system-ui;padding:24px">Preparing secure authorization…</p>'
    action.mutate('authorize_oauth', {
      onSuccess: (result) => {
        if (result.authorization_url) {
          if (popup) popup.location.href = result.authorization_url
          else window.open(result.authorization_url, '_blank', 'noopener,noreferrer')
        } else popup?.close()
      },
      onError: () => popup?.close(),
    })
  }

  const discoveredTools = test.data?.tools || tools.data?.tools || []
  const actionError = action.error instanceof Error ? action.error.message : setup.data?.error

  return (
    <div className="stack">
      {setup.data?.configured && (
        <section className="card">
          <header className="card__header">
            <div>
              <h3>Setup & authorization</h3>
              <p>Complete any one-time requirements before testing the MCP connection.</p>
            </div>
            <Status value={setup.data.status.kind} label={setup.data.status.text} />
          </header>
          <div className="card__body stack">
            {setup.isLoading && <Loading />}
            {setup.data?.oauth.enabled && (
              <div className="setup-method-row">
                <span className="setup-method-row__icon">
                  <KeyRound size={16} />
                </span>
                <span>
                  <strong>OAuth account</strong>
                  <small>
                    {setup.data.oauth.connected
                      ? 'Authorization is saved and refreshed through the MCP standard.'
                      : 'Authorize Mounir using the sign-in page published by this MCP server.'}
                  </small>
                </span>
                <div className="setup-method-row__actions">
                  {setup.data.oauth.connected && (
                    <Button
                      icon={<Unplug size={13} />}
                      busy={action.isPending && action.variables === 'disconnect_oauth'}
                      disabled={action.isPending}
                      onClick={() => action.mutate('disconnect_oauth')}
                    >
                      Disconnect
                    </Button>
                  )}
                  <Button
                    variant="primary"
                    icon={<KeyRound size={13} />}
                    busy={action.isPending && action.variables === 'authorize_oauth'}
                    disabled={action.isPending || setup.data.oauth.in_progress}
                    onClick={authorize}
                  >
                    {setup.data.oauth.connected ? 'Reconnect' : 'Connect OAuth'}
                  </Button>
                </div>
              </div>
            )}
            {setup.data?.command.configured && (
              <div className="setup-method-row">
                <span className="setup-method-row__icon">
                  {server.managed ? <RefreshCw size={16} /> : <Play size={16} />}
                </span>
                <span>
                  <strong>{server.managed ? 'Automatic setup' : 'Setup command'}</strong>
                  <small>
                    {server.managed
                      ? 'Mounir sets up GBrain automatically at startup. Retry only if setup failed.'
                      : 'Runs the exact command saved in this server configuration.'}
                  </small>
                </span>
                <div className="setup-method-row__actions">
                  <Button
                    variant="primary"
                    icon={server.managed ? <RefreshCw size={13} /> : <Play size={13} />}
                    busy={action.isPending && action.variables === 'run_command'}
                    disabled={action.isPending}
                    onClick={() => action.mutate('run_command')}
                  >
                    {server.managed ? 'Retry setup' : 'Run setup'}
                  </Button>
                </div>
              </div>
            )}
            {!!setup.data?.credential_files.length && (
              <div className="setup-files-summary">
                <strong>Private files</strong>
                <div className="chips">
                  {setup.data.credential_files.map((file) => (
                    <span className="chip" key={file.env_var} title={file.filename}>
                      {file.env_var}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <Feedback message={actionError} />
            <Feedback
              kind="success"
              message={action.data?.message && !actionError ? action.data.message : ''}
            />
          </div>
        </section>
      )}

      <section className="card">
        <header className="card__header">
          <div>
            <h3>Connection & tools</h3>
            <p>Connect now to verify the configuration and discover available tools.</p>
          </div>
          <Button
            icon={<RefreshCw size={13} />}
            onClick={() => test.mutate()}
            busy={test.isPending}
          >
            Test connection
          </Button>
        </header>
        <div className="card__body">
          {tools.isLoading ? (
            <Loading />
          ) : (
            <>
              <div className="server-test-status">
                <Feedback
                  message={
                    (test.error || tools.error) instanceof Error
                      ? (test.error || (tools.error as Error)).message
                      : undefined
                  }
                />
              </div>
              {!discoveredTools.length && <p className="empty-inline">No tools discovered yet.</p>}
              <div className="tool-list">
                {discoveredTools.map((tool) => (
                  <div className="tool-option tool-option--readonly" key={tool.name}>
                    <span>
                      <strong>{readable(tool.name)}</strong>
                      <small>{tool.description || 'No description available.'}</small>
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  )
}
