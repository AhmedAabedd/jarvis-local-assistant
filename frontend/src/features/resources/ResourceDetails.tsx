import { BookOpen, Settings2, ShieldCheck, Wrench } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { McpServer, ModelRecord, SkillRecord, Subagent } from '../../api/types'
import { SectionTabs } from '../../components/ui/SectionTabs'
import { Status } from '../../components/ui/Status'
import { objectValue, readable, stringList } from './helpers'
import { expandLegacyToolRules, selectedToolOptions, useMcpToolCatalog } from './McpToolAccess'
import { ServerTools } from './ServerTools'
import { SkillPicker } from './SkillPicker'
import { ToolChoices } from './ToolChoices'

type AgentDetailsPage = 'configuration' | 'skills' | 'tools' | 'security'

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
  skills,
}: {
  kind: 'models' | 'servers' | 'agents'
  item: ModelRecord | McpServer | Subagent
  models: ModelRecord[]
  skills: SkillRecord[]
}) {
  if (kind === 'models') {
    const model = item as ModelRecord
    return (
      <section className="card resource-workspace">
        <div className="card__body detail-grid">
          <Detail label="Name" value={model.name} />
          <Detail label="Provider" value={model.provider_name || model.provider} />
          <Detail label="Model ID" value={model.model} full mono />
          <Detail
            label={model.provider_base_url_name || 'Base URL'}
            value={model.base_url}
            full
            mono
          />
          <Detail
            label="Credential"
            value={
              model.provider_api_key_name ||
              (model.api_key_configured || model.api_key ? 'API key saved' : 'No API key saved')
            }
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
        ? [
            ...Object.keys(objectValue(server.env)),
            ...(server.credential_files || []).map((file) => file.env_var),
          ]
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
            {server.transport !== 'stdio' && (
              <Detail
                label="Authentication"
                value={
                  server.auth_scheme === 'oauth'
                    ? server.oauth_connected
                      ? 'OAuth connected'
                      : 'OAuth authorization required'
                    : readable(server.auth_scheme || 'none')
                }
              />
            )}
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
  return <ReadonlyAgentDetails agent={item as Subagent} models={models} skills={skills} />
}

function ReadonlyAgentDetails({
  agent,
  models,
  skills,
}: {
  agent: Subagent
  models: ModelRecord[]
  skills: SkillRecord[]
}) {
  const [page, setPage] = useState<AgentDetailsPage>('configuration')
  const enabled = !(agent.enabled === false || Number(agent.enabled) === 0)
  const model = models.find((v) => v.id === Number(agent.model_id))
  const sources = agent.mcp_sources || []
  const assignedSkillIds = useMemo(
    () => new Set((agent.skill_ids || []).map(Number)),
    [agent.skill_ids],
  )
  const assignedSkills = useMemo(
    () => skills.filter((skill) => assignedSkillIds.has(Number(skill.id))),
    [assignedSkillIds, skills],
  )
  const toolGroups = useMcpToolCatalog(
    sources.map((source) => ({
      id: source.mcp_server_id,
      name: source.mcp_server_name || `Server ${source.mcp_server_id}`,
      connection_status: source.connection_status,
    })),
  )
  const enabledTools = useMemo(
    () => selectedToolOptions(toolGroups, sources),
    [toolGroups, sources],
  )
  const confirms = stringList(agent.confirm_tools, agent.confirm_tool_calls ? ['*'] : [])
  const dedupe = stringList(agent.dedupe_tools)
  const confirmedRules = expandLegacyToolRules(
    confirms.filter((name) => name !== '*'),
    enabledTools,
  )
  const dedupeRules = expandLegacyToolRules(dedupe, enabledTools)
  const confirmedTools = confirms.includes('*')
    ? enabledTools
    : enabledTools.filter((tool) => confirmedRules.has(tool.name))
  const dedupedTools = enabledTools.filter((tool) => dedupeRules.has(tool.name))
  return (
    <div className="stack">
      <section className="card resource-workspace resource-workspace--readonly">
        <div className="setting-row readonly-setting-row">
          <span>
            <strong>{enabled ? 'Active' : 'Inactive'}</strong>
            <small>Controls whether this subagent can receive delegated tasks.</small>
          </span>
        </div>
        <div className="card__body detail-grid resource-detail-body">
          <SectionTabs
            className="subagent-form-tabs builtin-agent-tabs"
            label="Subagent details sections"
            value={page}
            options={[
              { id: 'configuration', label: 'Configuration', icon: <Settings2 size={14} /> },
              {
                id: 'skills',
                label: 'Skills',
                icon: <BookOpen size={14} />,
                count: assignedSkills.length,
              },
              {
                id: 'tools',
                label: 'Tools',
                icon: <Wrench size={14} />,
                count: enabledTools.length,
              },
              { id: 'security', label: 'Security', icon: <ShieldCheck size={14} /> },
            ]}
            onChange={(value) => setPage(value as AgentDetailsPage)}
          />
          {page === 'configuration' && (
            <div className="detail detail--full detail-grid">
              <Detail
                label="Model"
                value={model ? `${model.name} — ${model.model}` : agent.model_name}
              />
              <Detail
                label="Capabilities"
                value={
                  sources.length
                    ? `${sources.length} MCP server${sources.length === 1 ? '' : 's'}`
                    : 'Prompt only'
                }
              />
              <Detail
                label="Workflow placements"
                value={agent.placement_count || 'Not connected'}
              />
              <Detail label="Description" value={agent.description} full />
              <Detail
                label="System prompt"
                value={agent.system_prompt || 'Default instructions'}
                full
              />
            </div>
          )}
          {page === 'skills' && (
            <div className="detail detail--full subagent-wizard__page">
              <p className="subagent-wizard__section-description">
                Skills available to this subagent. Open Edit to change assignments.
              </p>
              <SkillPicker
                skills={assignedSkills}
                selected={assignedSkillIds}
                readOnly
                empty="No skills are assigned to this subagent."
              />
            </div>
          )}
          {page === 'tools' && (
            <div className="detail detail--full subagent-wizard__page">
              <p className="subagent-wizard__section-description">
                Tools currently available from this subagent’s selected MCP sources.
              </p>
              <ToolChoices
                tools={enabledTools}
                readOnly
                empty={sources.length ? 'No tools are currently available.' : 'No external tools.'}
              />
            </div>
          )}
          {page === 'security' && (
            <div className="detail detail--full readonly-security">
              <Detail
                label="Action confirmation"
                value={
                  confirms.includes('*')
                    ? 'Ask before every action'
                    : confirms.length
                      ? 'Ask for selected actions'
                      : 'Run without confirmation'
                }
                full
              />
              {confirms.length > 0 && (
                <div>
                  <span className="field__label">Actions requiring confirmation</span>
                  <ToolChoices
                    tools={confirmedTools}
                    readOnly
                    empty="The saved confirmation tools are not currently available."
                  />
                </div>
              )}
              <div>
                <span className="field__label">Repeated action protection</span>
                <ToolChoices
                  tools={dedupedTools}
                  readOnly
                  empty="No repeated-action protection is configured."
                />
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
