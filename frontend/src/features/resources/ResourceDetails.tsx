import type { McpServer, ModelRecord, Subagent } from '../../api/types'
import { Status } from '../../components/ui/Status'
import { objectValue, readable, stringList } from './helpers'
import { ServerTools } from './ServerTools'

function Detail({
  label,
  value,
  full,
  mono,
}: {
  label: string
  value?: unknown
  full?: boolean
  mono?: boolean
}) {
  return (
    <div className={`detail ${full ? 'detail--full' : ''}`}>
      <dt>{label}</dt>
      <dd className={mono ? 'mono' : ''}>{String(value || 'Not configured')}</dd>
    </div>
  )
}

export function ResourceDetails({
  kind,
  item,
  models,
  servers,
  onToggle,
}: {
  kind: 'models' | 'servers' | 'agents'
  item: ModelRecord | McpServer | Subagent
  models: ModelRecord[]
  servers: McpServer[]
  onToggle: (enabled: boolean) => void
}) {
  if (kind === 'models') {
    const model = item as ModelRecord
    return (
      <section className="card resource-workspace">
        <div className="card__body detail-grid">
          <Detail label="Name" value={model.name} />
          <Detail label="Provider" value={model.provider} />
          <Detail label="Model ID" value={model.model} full mono />
          <Detail label="Base URL" value={model.base_url} full mono />
          <Detail
            label="Credential"
            value={model.api_key_configured || model.api_key ? 'API key saved' : 'No API key saved'}
            full
          />
        </div>
      </section>
    )
  }
  if (kind === 'servers') {
    const server = item as McpServer
    const credentials =
      server.transport === 'stdio'
        ? Object.keys(objectValue(server.env))
        : Object.keys(objectValue(server.headers))
    return (
      <div className="stack">
        <section className="card resource-workspace">
          <div className="card__body detail-grid">
            <Detail label="Name" value={server.name} />
            <div className="detail">
              <dt>Status</dt>
              <dd>
                <Status value={server.connection_status || 'untested'} />
              </dd>
            </div>
            <Detail label="Transport" value={readable(server.transport)} />
            <Detail label="Connection" value={server.connection} mono />
            <Detail label="Description" value={server.description} full />
            <div className="detail detail--full">
              <dt>Configured credentials</dt>
              <dd>
                <div className="chips">
                  {credentials.length ? (
                    credentials.map((name) => (
                      <span className="chip" key={name}>
                        {name}
                      </span>
                    ))
                  ) : (
                    <span className="chip">None</span>
                  )}
                </div>
              </dd>
            </div>
          </div>
        </section>
        <ServerTools server={server} />
      </div>
    )
  }
  const agent = item as Subagent
  const enabled = !(agent.enabled === false || Number(agent.enabled) === 0)
  const model = models.find((v) => v.id === Number(agent.model_id))
  const server = servers.find((v) => v.id === Number(agent.mcp_server_id))
  const confirms = stringList(agent.confirm_tools, agent.confirm_tool_calls ? ['*'] : [])
  const dedupe = stringList(agent.dedupe_tools)
  return (
    <div className="stack">
      <section className="card resource-workspace">
        <div className="setting-row">
          <span>
            <strong>{enabled ? 'Active' : 'Inactive'}</strong>
            <small>
              {enabled ? 'Available for supervisor delegation' : 'Removed from runtime delegation'}
            </small>
          </span>
          <label className="switch">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => onToggle(event.target.checked)}
            />
            <span className="switch__track">
              <span />
            </span>
          </label>
        </div>
        <div className="card__body detail-grid resource-detail-body">
          <Detail
            label="Model"
            value={model ? `${model.name} — ${model.model}` : agent.model_name}
          />
          <Detail label="MCP server" value={server?.name || agent.mcp_server_name} />
          <Detail label="Description" value={agent.description} full />
          <Detail
            label="System prompt"
            value={agent.system_prompt || 'Default instructions'}
            full
          />
          <div className="detail detail--full">
            <dt>Actions requiring confirmation</dt>
            <dd>
              <div className="chips">
                {confirms.length ? (
                  confirms.map((name) => (
                    <span className="chip" key={name}>
                      {name === '*' ? 'Every action' : readable(name)}
                    </span>
                  ))
                ) : (
                  <span className="chip">None</span>
                )}
              </div>
            </dd>
          </div>
          <div className="detail detail--full">
            <dt>Repeated action protection</dt>
            <dd>
              <div className="chips">
                {dedupe.length ? (
                  dedupe.map((name) => (
                    <span className="chip" key={name}>
                      {readable(name)}
                    </span>
                  ))
                ) : (
                  <span className="chip">None</span>
                )}
              </div>
            </dd>
          </div>
        </div>
      </section>
    </div>
  )
}
