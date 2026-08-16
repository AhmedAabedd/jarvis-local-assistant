import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronLeft, ChevronRight, Plus, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { McpServer, ModelRecord, Subagent } from '../../api/types'
import { AutoTextarea } from '../../components/ui/AutoTextarea'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { stringList, toDataUrl } from './helpers'
import { ToolChoices } from './ToolChoices'

type FormPage = 'configuration' | 'tools' | 'security'
type ConfirmationMode = 'all' | 'selected' | 'none'

export function AgentForm({
  item,
  models,
  servers,
  formId,
  busy,
  onSubmit,
}: {
  item?: Subagent
  models: ModelRecord[]
  servers: McpServer[]
  formId: string
  busy?: boolean
  onSubmit: (body: object) => Promise<void>
}) {
  const queryClient = useQueryClient()
  const existingConfirm = stringList(item?.confirm_tools, item?.confirm_tool_calls ? ['*'] : [])
  const [page, setPage] = useState<FormPage>('configuration')
  const [furthestPage, setFurthestPage] = useState(1)
  const [name, setName] = useState(item?.name || '')
  const [description, setDescription] = useState(item?.description || '')
  const [systemPrompt, setSystemPrompt] = useState(item?.system_prompt || '')
  const [modelId, setModelId] = useState(Number(item?.model_id || 0))
  const [serverId, setServerId] = useState(Number(item?.mcp_server_id || 0))
  const [mode, setMode] = useState<ConfirmationMode>(
    existingConfirm.includes('*') ? 'all' : existingConfirm.length ? 'selected' : 'none',
  )
  const [confirmed, setConfirmed] = useState(new Set(existingConfirm.filter((v) => v !== '*')))
  const [deduped, setDeduped] = useState(new Set(stringList(item?.dedupe_tools)))
  const [selectedTools, setSelectedTools] = useState(new Set(item?.enabled_tools || []))
  const [toolDefaults, setToolDefaults] = useState<string[] | null>(item?.enabled_tools ?? null)
  const [toolSelectionExplicit, setToolSelectionExplicit] = useState(item?.enabled_tools != null)
  const [initializedServerId, setInitializedServerId] = useState(0)
  const [icon, setIcon] = useState<string | undefined>()
  const [error, setError] = useState('')

  const tools = useQuery({
    queryKey: ['server-tools', serverId],
    queryFn: () => api.servers.tools(serverId),
    enabled: Boolean(serverId),
  })
  const refreshTools = useMutation({
    mutationFn: () => api.servers.test(serverId),
    onSuccess: (state) => queryClient.setQueryData(['server-tools', serverId], state),
  })
  const availableTools = tools.data?.tools || []
  const availableNames = useMemo(() => availableTools.map((tool) => tool.name), [availableTools])
  const enabledTools = useMemo(
    () => availableTools.filter((tool) => selectedTools.has(tool.name)),
    [availableTools, selectedTools],
  )
  const enabledNames = useMemo(() => enabledTools.map((tool) => tool.name), [enabledTools])

  useEffect(() => {
    if (!serverId || tools.data === undefined) return
    if (initializedServerId !== serverId) {
      const defaults = toolDefaults === null ? availableNames : toolDefaults
      setSelectedTools(new Set(defaults.filter((tool) => availableNames.includes(tool))))
      setInitializedServerId(serverId)
      return
    }
    if (
      !toolSelectionExplicit &&
      (selectedTools.size !== availableNames.length ||
        availableNames.some((tool) => !selectedTools.has(tool)))
    ) {
      setSelectedTools(new Set(availableNames))
    }
  }, [
    availableNames,
    initializedServerId,
    selectedTools,
    serverId,
    toolDefaults,
    toolSelectionExplicit,
    tools.data,
  ])

  const selectServer = (nextServerId: number) => {
    setServerId(nextServerId)
    setToolDefaults(null)
    setToolSelectionExplicit(false)
    setInitializedServerId(0)
    setSelectedTools(new Set())
    setMode('all')
    setConfirmed(new Set())
    setDeduped(new Set())
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    try {
      if (!name.trim()) throw new Error('Enter a name for the subagent.')
      if (!description.trim()) throw new Error('Enter a description for the subagent.')
      if (!modelId) throw new Error('Choose a model for the subagent.')
      if (!serverId) throw new Error('Choose an MCP server for the subagent.')

      const hasToolInventory = tools.data !== undefined
      const confirmedTools = hasToolInventory
        ? [...confirmed].filter((tool) => enabledNames.includes(tool))
        : [...confirmed]
      const dedupedTools = hasToolInventory
        ? [...deduped].filter((tool) => enabledNames.includes(tool))
        : [...deduped]
      if (mode === 'selected' && !confirmedTools.length) {
        setPage('security')
        throw new Error('Select at least one action requiring confirmation.')
      }
      const allToolsEnabled =
        availableNames.length > 0 &&
        availableNames.length === selectedTools.size &&
        availableNames.every((tool) => selectedTools.has(tool))
      await onSubmit({
        name: name.trim(),
        description: description.trim(),
        system_prompt: systemPrompt.trim(),
        model_id: modelId,
        mcp_server_id: serverId,
        enabled_tools: !toolSelectionExplicit || allToolsEnabled ? null : [...selectedTools],
        confirm_tools: mode === 'all' ? ['*'] : mode === 'selected' ? confirmedTools : [],
        confirm_tool_calls: mode !== 'none',
        dedupe_tools: dedupedTools,
        ...(icon !== undefined ? { icon_data: icon } : {}),
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save the subagent.')
    }
  }

  const pages: Array<{ id: FormPage; label: string }> = [
    { id: 'configuration', label: 'Configuration' },
    { id: 'tools', label: 'Tools' },
    { id: 'security', label: 'Security' },
  ]
  const currentPageIndex = pages.findIndex((entry) => entry.id === page)
  const hasIcon = Boolean(icon) || (icon === undefined && Boolean(item?.has_icon))
  const openPage = (nextPage: FormPage, pageNumber: number) => {
    setError('')
    if (!item && pageNumber > 1) {
      if (!name.trim()) return setError('Enter a name for the subagent.')
      if (!description.trim()) return setError('Enter a description for the subagent.')
      if (!modelId) return setError('Choose a model for the subagent.')
      if (!serverId) return setError('Choose an MCP server for the subagent.')
    }
    setPage(nextPage)
    setFurthestPage((current) => Math.max(current, pageNumber))
  }

  return (
    <form id={formId} className="subagent-edit-form" onSubmit={submit}>
      {item ? (
        <nav className="subagent-form-tabs" aria-label="Subagent configuration sections">
          {pages.map((entry, index) => (
            <button
              className={page === entry.id ? 'is-active' : ''}
              type="button"
              key={entry.id}
              aria-current={page === entry.id ? 'page' : undefined}
              onClick={() => openPage(entry.id, index + 1)}
            >
              {entry.label}
            </button>
          ))}
        </nav>
      ) : (
        <nav className="subagent-wizard__steps" aria-label="Creation progress">
          {pages.map((entry, index) => {
            const pageNumber = index + 1
            return (
              <button
                className={`subagent-wizard__step ${page === entry.id ? 'is-current' : ''} ${furthestPage > pageNumber ? 'is-complete' : ''}`}
                type="button"
                key={entry.id}
                aria-current={page === entry.id ? 'step' : undefined}
                onClick={() => openPage(entry.id, pageNumber)}
              >
                <span className="subagent-wizard__step-marker">
                  {furthestPage > pageNumber ? <Check size={13} /> : pageNumber}
                </span>
                <small>{entry.label}</small>
              </button>
            )
          })}
        </nav>
      )}

      {page === 'configuration' && (
        <div className="form-grid subagent-wizard__page">
          {!models.length && <div className="guidance">Create a model first.</div>}
          {!servers.length && <div className="guidance">Connect an MCP server first.</div>}
          <Field full label="Name" hint="Use a unique name for this specific role.">
            <input value={name} onChange={(event) => setName(event.target.value)} autoFocus />
          </Field>
          <Field full label="Icon" hint="Square PNG, JPEG, WebP, or GIF smaller than 512 KB.">
            <div className="subagent-wizard__icon-field">
              <span className={`avatar ${hasIcon ? 'has-image' : ''}`}>
                {icon ? (
                  <img src={icon} alt="Preview" />
                ) : icon === undefined && item?.has_icon ? (
                  <img src={`/api/subagents/${item.id}/icon`} alt="Current" />
                ) : (
                  (name || '?').charAt(0).toUpperCase()
                )}
              </span>
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                onChange={async (event) => {
                  const file = event.target.files?.[0]
                  if (!file) return
                  if (file.size > 512 * 1024) {
                    setError('The icon must be smaller than 512 KB.')
                    return
                  }
                  setIcon(await toDataUrl(file))
                }}
              />
              {hasIcon && (
                <Button type="button" onClick={() => setIcon('')}>
                  Remove
                </Button>
              )}
            </div>
          </Field>
          <Field
            full
            label="Description"
            hint="Explain exactly when the parent should delegate here."
          >
            <AutoTextarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={2}
            />
          </Field>
          <Field full label="System prompt" hint="Instructions that apply only to this subagent.">
            <AutoTextarea
              value={systemPrompt}
              onChange={(event) => setSystemPrompt(event.target.value)}
              rows={5}
            />
          </Field>
          <Field label="Model">
            <select
              value={modelId || ''}
              onChange={(event) => setModelId(Number(event.target.value))}
            >
              <option value="">Choose a model…</option>
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name} — {model.provider}
                </option>
              ))}
            </select>
          </Field>
          <Field label="MCP server">
            <select
              value={serverId || ''}
              onChange={(event) => selectServer(Number(event.target.value))}
            >
              <option value="">Choose a server…</option>
              {servers.map((server) => (
                <option key={server.id} value={server.id}>
                  {server.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}

      {page === 'tools' && (
        <div className="subagent-wizard__page">
          <div className="subagent-wizard__section-heading">
            <span>
              <strong>Available tools</strong>
              <small>Choose which MCP actions this subagent can use.</small>
            </span>
            <div>
              <button
                type="button"
                onClick={() => {
                  setToolSelectionExplicit(false)
                  setSelectedTools(new Set(availableNames))
                }}
              >
                Enable all
              </button>
              <button
                type="button"
                onClick={() => {
                  setToolSelectionExplicit(true)
                  setSelectedTools(new Set())
                }}
              >
                Disable all
              </button>
              <button
                type="button"
                onClick={() => refreshTools.mutate()}
                disabled={!serverId || refreshTools.isPending}
              >
                <RefreshCw size={12} /> Refresh
              </button>
            </div>
          </div>
          {tools.isLoading ? (
            <Loading label="Loading tools…" />
          ) : (
            <ToolChoices
              tools={availableTools}
              selected={selectedTools}
              onChange={(selection) => {
                setToolSelectionExplicit(true)
                setSelectedTools(selection)
              }}
            />
          )}
        </div>
      )}

      {page === 'security' && (
        <div className="form-grid subagent-wizard__page">
          <Field
            full
            label="Action confirmation"
            hint="Control which actions require your approval before they run."
          >
            <select
              value={mode}
              onChange={(event) => setMode(event.target.value as ConfirmationMode)}
            >
              <option value="all">Ask before every action</option>
              <option value="selected">Ask for selected actions</option>
              <option value="none">Run without confirmation</option>
            </select>
          </Field>
          {mode === 'selected' && (
            <div className="key-value-editor">
              <div className="key-value-editor__title">
                <span>
                  <strong>Actions requiring confirmation</strong>
                  <small>Select the actions Mounir must ask you to approve.</small>
                </span>
              </div>
              <ToolChoices
                tools={enabledTools}
                selected={confirmed}
                onChange={setConfirmed}
                empty="Enable at least one tool before configuring confirmation rules."
              />
            </div>
          )}
          <div className="key-value-editor">
            <div className="key-value-editor__title">
              <span>
                <strong>Repeated action protection</strong>
                <small>Block an identical action from running twice during the same request.</small>
              </span>
            </div>
            <ToolChoices
              tools={enabledTools}
              selected={deduped}
              onChange={setDeduped}
              empty="Enable at least one tool before configuring repeated-action protection."
            />
          </div>
        </div>
      )}

      <Feedback
        message={
          error ||
          (tools.error instanceof Error ? tools.error.message : '') ||
          (refreshTools.error instanceof Error ? refreshTools.error.message : '')
        }
      />

      {!item && (
        <div className="subagent-form-footer">
          {currentPageIndex > 0 && (
            <Button
              type="button"
              icon={<ChevronLeft size={13} />}
              disabled={busy}
              onClick={() => openPage(pages[currentPageIndex - 1].id, currentPageIndex)}
            >
              Previous
            </Button>
          )}
          {currentPageIndex < pages.length - 1 ? (
            <Button
              type="button"
              variant="primary"
              disabled={busy}
              onClick={() => openPage(pages[currentPageIndex + 1].id, currentPageIndex + 2)}
            >
              Next <ChevronRight size={13} />
            </Button>
          ) : (
            <Button type="submit" variant="primary" icon={<Plus size={14} />} busy={busy}>
              Create subagent
            </Button>
          )}
        </div>
      )}
    </form>
  )
}
