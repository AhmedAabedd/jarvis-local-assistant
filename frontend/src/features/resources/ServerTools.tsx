import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Upload } from 'lucide-react'
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
  const test = useMutation({
    mutationFn: () => api.servers.test(server.id),
    onSuccess: (data) => client.setQueryData(key, data),
  })
  const setup = useQuery({
    queryKey: ['server-setup', server.id],
    queryFn: () => api.servers.setup(server.id),
    enabled: Boolean(server.setup_type),
    retry: false,
  })
  const action = useMutation({
    mutationFn: (id: string) => api.servers.setupAction(server.id, id),
    onSuccess: () => setup.refetch(),
  })
  const upload = useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) =>
      api.servers.setupFile(server.id, id, file),
    onSuccess: () => setup.refetch(),
  })
  return (
    <div className="stack">
      <section className="card">
        <header className="card__header">
          <div>
            <h3>Discovered tools</h3>
            <p>Cached capabilities published by this MCP server.</p>
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
              <Status
                value={
                  test.data?.status || tools.data?.status || server.connection_status || 'untested'
                }
              />
              <Feedback
                message={
                  (test.error || tools.error) instanceof Error
                    ? (test.error || (tools.error as Error)).message
                    : undefined
                }
              />
              <div className="tool-list">
                {(test.data?.tools || tools.data?.tools || []).map((tool) => (
                  <div className="tool-option" key={tool.name}>
                    <span>•</span>
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
      {server.setup_type && (
        <section className="card">
          <header className="card__header">
            <div>
              <h3>{setup.data?.title || 'Additional setup'}</h3>
              <p>{setup.data?.description || 'Loading setup information…'}</p>
            </div>
            {setup.data && <Status value={setup.data.status.kind} label={setup.data.status.text} />}
          </header>
          <div className="card__body stack">
            {setup.isLoading && <Loading />}
            <Feedback message={setup.error instanceof Error ? setup.error.message : undefined} />
            <div className="page-actions">
              {setup.data?.file_actions.map((item) => (
                <label className="button button--secondary" key={item.id}>
                  <Upload size={13} />
                  {upload.isPending ? item.busy_label : item.label}
                  <input
                    className="sr-only"
                    type="file"
                    accept={item.accept}
                    onChange={(e) => {
                      const file = e.target.files?.[0]
                      if (file) upload.mutate({ id: item.id, file })
                    }}
                  />
                </label>
              ))}
              {setup.data?.actions.map((item) => (
                <Button
                  key={item.id}
                  variant={item.style === 'primary' ? 'primary' : 'secondary'}
                  disabled={item.disabled}
                  busy={action.isPending}
                  onClick={() => action.mutate(item.id)}
                >
                  {item.label}
                </Button>
              ))}
            </div>
            <Feedback
              message={
                (action.error || upload.error) instanceof Error
                  ? (action.error || (upload.error as Error)).message
                  : undefined
              }
            />
          </div>
        </section>
      )}
    </div>
  )
}
