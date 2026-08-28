import { BookOpen, Check, ChevronLeft, Settings2, ShieldCheck, Wrench } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import type { McpServer, ModelRecord, Subagent, SubagentMcpSource } from '../../api/types'
import { AutoTextarea } from '../../components/ui/AutoTextarea'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { SectionTabs } from '../../components/ui/SectionTabs'
import { useSkills } from '../../hooks/useStudioData'
import { stringList, toDataUrl } from './helpers'
import {
  expandLegacyToolRules,
  MultiServerToolPicker,
  selectedToolOptions,
  useMcpToolCatalog,
} from './McpToolAccess'
import { ToolChoices } from './ToolChoices'
import { SkillPicker } from './SkillPicker'

type FormPage = 'configuration' | 'skills' | 'tools' | 'security'
type ConfirmationMode = 'all' | 'selected' | 'none'

export function AgentForm({
  item,
  models,
  servers,
  formId,
  busy,
  onSubmit,
  onDirtyChange,
}: {
  item?: Subagent
  models: ModelRecord[]
  servers: McpServer[]
  formId: string
  busy?: boolean
  onSubmit: (body: object) => Promise<void>
  onDirtyChange?: (dirty: boolean) => void
}) {
  const formRef = useRef<HTMLFormElement>(null)
  const existingConfirm = stringList(
    item?.confirm_tools,
    item ? (item.confirm_tool_calls ? ['*'] : []) : ['*'],
  )
  const [page, setPage] = useState<FormPage>('configuration')
  const [furthestPage, setFurthestPage] = useState(1)
  const [name, setName] = useState(item?.name || '')
  const [description, setDescription] = useState(item?.description || '')
  const [systemPrompt, setSystemPrompt] = useState(item?.system_prompt || '')
  const [modelId, setModelId] = useState(Number(item?.model_id || 0))
  const [sources, setSources] = useState<SubagentMcpSource[]>(
    item?.mcp_sources?.length
      ? item.mcp_sources
      : item?.mcp_server_id
        ? [{ mcp_server_id: Number(item.mcp_server_id), enabled_tools: item.enabled_tools }]
        : [],
  )
  const skills = useSkills()
  const [selectedSkills, setSelectedSkills] = useState(new Set((item?.skill_ids || []).map(Number)))
  const [mode, setMode] = useState<ConfirmationMode>(
    existingConfirm.includes('*') ? 'all' : existingConfirm.length ? 'selected' : 'none',
  )
  const [confirmed, setConfirmed] = useState(new Set(existingConfirm.filter((v) => v !== '*')))
  const [deduped, setDeduped] = useState(new Set(stringList(item?.dedupe_tools)))
  const [icon, setIcon] = useState<string | undefined>()
  const [error, setError] = useState('')

  const toolGroups = useMcpToolCatalog(servers)
  const enabledTools = useMemo(
    () => selectedToolOptions(toolGroups, sources),
    [toolGroups, sources],
  )
  const enabledNames = useMemo(() => enabledTools.map((tool) => tool.name), [enabledTools])
  const selectedSourceIds = useMemo(
    () => new Set(sources.map((source) => Number(source.mcp_server_id))),
    [sources],
  )
  const selectedCatalogIsComplete = useMemo(
    () =>
      sources.every((source) => {
        const group = toolGroups.find(
          (entry) => Number(entry.server.id) === Number(source.mcp_server_id),
        )
        return Boolean(group && !group.loading && !group.error && group.tools.length)
      }),
    [sources, toolGroups],
  )
  const confirmedSelection = useMemo(
    () => expandLegacyToolRules([...confirmed], enabledTools),
    [confirmed, enabledTools],
  )
  const dedupedSelection = useMemo(
    () => expandLegacyToolRules([...deduped], enabledTools),
    [deduped, enabledTools],
  )

  const canonicalSources = (values: SubagentMcpSource[]) =>
    values
      .map((source) => ({
        mcp_server_id: Number(source.mcp_server_id),
        enabled_tools:
          source.enabled_tools === null
            ? null
            : [...(source.enabled_tools || [])].map(String).sort(),
      }))
      .sort((left, right) => left.mcp_server_id - right.mcp_server_id)

  const dirty = useMemo(() => {
    if (!item) return false
    const originalSources = item.mcp_sources?.length
      ? item.mcp_sources
      : item.mcp_server_id
        ? [{ mcp_server_id: Number(item.mcp_server_id), enabled_tools: item.enabled_tools }]
        : []
    const nextConfirm = mode === 'all' ? ['*'] : mode === 'selected' ? [...confirmed].sort() : []
    const originalConfirm = [...existingConfirm].sort()
    const nextDedupe = [...deduped].sort()
    const originalDedupe = stringList(item.dedupe_tools).sort()
    const nextSkills = [...selectedSkills].sort((left, right) => left - right)
    const originalSkills = [...(item.skill_ids || [])]
      .map(Number)
      .sort((left, right) => left - right)
    const iconDirty = icon !== undefined && (icon !== '' || Boolean(item.has_icon))
    return (
      name.trim() !== item.name ||
      description.trim() !== item.description ||
      systemPrompt.trim() !== item.system_prompt ||
      modelId !== Number(item.model_id || 0) ||
      JSON.stringify(canonicalSources(sources)) !==
        JSON.stringify(canonicalSources(originalSources)) ||
      JSON.stringify(nextSkills) !== JSON.stringify(originalSkills) ||
      JSON.stringify(nextConfirm) !== JSON.stringify(originalConfirm) ||
      JSON.stringify(nextDedupe) !== JSON.stringify(originalDedupe) ||
      iconDirty
    )
  }, [
    confirmed,
    deduped,
    description,
    existingConfirm,
    icon,
    item,
    mode,
    modelId,
    name,
    selectedSkills,
    sources,
    systemPrompt,
  ])

  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange])
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])

  const savedRulesForSelectedSources = (rules: Set<string>) =>
    [...rules].filter((rule) => {
      const separator = rule.indexOf(':')
      if (separator < 1) return true
      const serverId = Number(rule.slice(0, separator))
      return !Number.isFinite(serverId) || selectedSourceIds.has(serverId)
    })

  const pages: Array<{ id: FormPage; label: string }> = [
    { id: 'configuration', label: 'Configuration' },
    { id: 'skills', label: 'Skills' },
    { id: 'tools', label: 'Tools' },
    { id: 'security', label: 'Security' },
  ]
  const currentPageIndex = pages.findIndex((entry) => entry.id === page)
  const hasIcon = Boolean(icon) || (icon === undefined && Boolean(item?.has_icon))
  const openPage = (nextPage: FormPage) => {
    const nextPageIndex = pages.findIndex((entry) => entry.id === nextPage)
    if (nextPageIndex < 0) return
    const pageNumber = nextPageIndex + 1
    setError('')
    if (!item && pageNumber > 1) {
      if (!name.trim()) return setError('Enter a name for the subagent.')
      if (!description.trim()) return setError('Enter a description for the subagent.')
      if (!modelId) return setError('Choose a model for the subagent.')
    }
    setPage(nextPage)
    setFurthestPage((current) => Math.max(current, pageNumber))
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    if (!item && page !== 'security') {
      const nextPage = pages[currentPageIndex + 1]?.id
      if (nextPage) openPage(nextPage)
      return
    }
    try {
      if (!name.trim()) throw new Error('Enter a name for the subagent.')
      if (!description.trim()) throw new Error('Enter a description for the subagent.')
      if (!modelId) throw new Error('Choose a model for the subagent.')
      const confirmedTools = selectedCatalogIsComplete
        ? [...confirmedSelection].filter((tool) => enabledNames.includes(tool))
        : savedRulesForSelectedSources(confirmedSelection)
      const dedupedTools = selectedCatalogIsComplete
        ? [...dedupedSelection].filter((tool) => enabledNames.includes(tool))
        : savedRulesForSelectedSources(dedupedSelection)
      if (sources.length && mode === 'selected' && !confirmedTools.length) {
        setPage('security')
        throw new Error(
          enabledTools.length
            ? 'Select at least one action requiring confirmation.'
            : 'Refresh the selected MCP servers before choosing individual confirmation rules.',
        )
      }
      await onSubmit({
        name: name.trim(),
        description: description.trim(),
        system_prompt: systemPrompt.trim(),
        model_id: modelId,
        mcp_sources: sources.map(({ mcp_server_id, enabled_tools }) => ({
          mcp_server_id,
          enabled_tools,
        })),
        skill_ids: [...selectedSkills].sort((left, right) => left - right),
        confirm_tools: !sources.length
          ? []
          : mode === 'all'
            ? ['*']
            : mode === 'selected'
              ? confirmedTools
              : [],
        confirm_tool_calls: Boolean(sources.length) && mode !== 'none',
        dedupe_tools: dedupedTools,
        ...(icon !== undefined ? { icon_data: icon } : {}),
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save the subagent.')
    }
  }

  useEffect(() => {
    formRef.current?.closest('.modal__body')?.scrollTo({ top: 0 })
  }, [page])

  return (
    <form
      ref={formRef}
      id={formId}
      className={`subagent-edit-form ${item ? 'subagent-edit-form--editing' : 'subagent-edit-form--creating'} ${page === 'skills' || page === 'tools' ? 'subagent-edit-form--list-page' : ''}`}
      onSubmit={submit}
    >
      {item ? (
        <SectionTabs
          className="subagent-form-tabs"
          label="Subagent configuration sections"
          value={page}
          options={[
            { id: 'configuration', label: 'Configuration', icon: <Settings2 size={14} /> },
            {
              id: 'skills',
              label: 'Skills',
              icon: <BookOpen size={14} />,
              count: selectedSkills.size,
            },
            {
              id: 'tools',
              label: 'Tools',
              icon: <Wrench size={14} />,
              count: enabledTools.length,
            },
            { id: 'security', label: 'Security', icon: <ShieldCheck size={14} /> },
          ]}
          onChange={(value) => openPage(value as FormPage)}
        />
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
                onClick={() => openPage(entry.id)}
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
        <div className="form-grid subagent-wizard__page subagent-wizard__page--configuration">
          {!models.length && <div className="guidance">Create a model first.</div>}
          {!servers.length && (
            <div className="guidance">
              No MCP servers are connected. You can still create a prompt-only subagent.
            </div>
          )}
          <Field label="Name" hint="Use a unique name for this specific role.">
            <input value={name} onChange={(event) => setName(event.target.value)} autoFocus />
          </Field>
          <Field label="Model" hint="Choose the saved model this subagent will use.">
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
        </div>
      )}

      {page === 'tools' && (
        <div className="subagent-wizard__page subagent-wizard__page--tools">
          <p className={`${item ? '' : 'guidance '}subagent-wizard__section-description`}>
            Choose tools from available MCP servers to connect them to your subagent.
          </p>
          <MultiServerToolPicker
            servers={servers}
            groups={toolGroups}
            sources={sources}
            onChange={setSources}
          />
        </div>
      )}

      {page === 'skills' && (
        <div className="subagent-wizard__page subagent-wizard__page--skills">
          <p className={`${item ? '' : 'guidance '}subagent-wizard__section-description`}>
            Select the installed skills this subagent can discover and activate.
          </p>
          <SkillPicker
            skills={skills.data || []}
            selected={selectedSkills}
            loading={skills.isLoading}
            error={skills.error instanceof Error ? skills.error.message : ''}
            onChange={setSelectedSkills}
          />
        </div>
      )}

      {page === 'security' && (
        <div className="form-grid subagent-wizard__page">
          {!sources.length && (
            <div className="guidance">
              Prompt-only subagents have no external actions to approve. You can save this
              configuration as-is.
            </div>
          )}
          <Field
            full
            label="Action confirmation"
            hint="Control which actions require your approval before they run."
          >
            <select
              value={mode}
              disabled={!sources.length}
              onChange={(event) => setMode(event.target.value as ConfirmationMode)}
            >
              <option value="all">Ask before every action</option>
              <option value="selected">Ask for selected actions</option>
              <option value="none">Run without confirmation</option>
            </select>
          </Field>
          {sources.length > 0 && mode === 'selected' && (
            <div className="key-value-editor">
              <div className="key-value-editor__title">
                <span>
                  <strong>Actions requiring confirmation</strong>
                  <small>Select the actions Mounir must ask you to approve.</small>
                </span>
              </div>
              <ToolChoices
                tools={enabledTools}
                selected={confirmedSelection}
                onChange={setConfirmed}
                empty="Enable at least one tool before configuring confirmation rules."
              />
            </div>
          )}
          {sources.length > 0 && (
            <div className="key-value-editor">
              <div className="key-value-editor__title">
                <span>
                  <strong>Repeated action protection</strong>
                  <small>
                    Block an identical action from running twice during the same request.
                  </small>
                </span>
              </div>
              <ToolChoices
                tools={enabledTools}
                selected={dedupedSelection}
                onChange={setDeduped}
                empty="Enable at least one tool before configuring repeated-action protection."
              />
            </div>
          )}
        </div>
      )}

      <Feedback message={error || ''} />

      {!item && (
        <div className="subagent-form-footer">
          {currentPageIndex > 0 && (
            <Button
              type="button"
              className="compact-form-back-button"
              icon={<ChevronLeft size={15} />}
              aria-label={`Back to ${pages[currentPageIndex - 1].label.toLowerCase()}`}
              title="Back"
              disabled={busy}
              onClick={() => openPage(pages[currentPageIndex - 1].id)}
            />
          )}
          {currentPageIndex < pages.length - 1 ? (
            <Button
              key="next-page"
              type="button"
              variant="primary"
              disabled={busy}
              onClick={(event) => {
                event.preventDefault()
                openPage(pages[currentPageIndex + 1].id)
              }}
            >
              Next
            </Button>
          ) : (
            <Button key="create-subagent" type="submit" variant="primary" busy={busy}>
              Create subagent
            </Button>
          )}
        </div>
      )}
    </form>
  )
}
