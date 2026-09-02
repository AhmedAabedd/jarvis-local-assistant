import {
  BookOpen,
  Code2,
  Play,
  RefreshCw,
  RotateCcw,
  Settings2,
  ShieldCheck,
  TriangleAlert,
  Wrench,
} from 'lucide-react'
import { forwardRef, useEffect, useImperativeHandle, useState } from 'react'
import { api } from '../../api/client'
import type { BuiltinAgent, EmbeddingModelRecord, ModelRecord, SkillRecord } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { SectionTabs } from '../../components/ui/SectionTabs'
import { Status } from '../../components/ui/Status'
import { stringList } from './helpers'
import { ToolChoices } from './ToolChoices'
import { SkillPicker } from './SkillPicker'

type DetailsPage = 'configuration' | 'skills' | 'tools' | 'security' | 'developer'
type ConfirmationMode = 'all' | 'selected' | 'none'

export interface BuiltinAgentDetailsHandle {
  save: () => void
}

interface BuiltinAgentDetailsProps {
  item: BuiltinAgent
  models: ModelRecord[]
  embeddingModels: EmbeddingModelRecord[]
  skills: SkillRecord[]
  skillsLoading?: boolean
  skillsError?: string
  saving?: boolean
  connecting?: boolean
  error?: string
  readOnly?: boolean
  compact?: boolean
  onConnect?: (connected: boolean) => void
  onDirtyChange?: (dirty: boolean) => void
  onSave?: (body: {
    model_id: number | null
    max_tool_rounds: number
    generation_model_id?: number | null
    automatic_knowledge_enabled?: boolean
    embedding_enabled?: boolean
    embedding_model_id?: number | null
    confirm_tools?: string[]
    skill_ids?: number[]
  }) => Promise<unknown>
}

function Detail({ label, value, full = false }: { label: string; value: string; full?: boolean }) {
  return (
    <div className={`detail ${full ? 'detail--full' : ''}`}>
      <dt>{label}</dt>
      <dd>{value || 'Not configured'}</dd>
    </div>
  )
}

export const BuiltinAgentDetails = forwardRef<BuiltinAgentDetailsHandle, BuiltinAgentDetailsProps>(
  function BuiltinAgentDetails(
    {
      item,
      models,
      embeddingModels,
      skills,
      skillsLoading,
      skillsError,
      saving,
      connecting,
      error,
      readOnly = false,
      compact = false,
      onConnect,
      onDirtyChange,
      onSave,
    },
    ref,
  ) {
    const existingConfirm = stringList(
      item.confirm_tools,
      item.tools?.filter((tool) => tool.requires_confirmation).map((tool) => tool.name) || [],
    )
    const [page, setPage] = useState<DetailsPage>('configuration')
    const [modelId, setModelId] = useState(Number(item.model_id || 0))
    const [maxToolRounds, setMaxToolRounds] = useState(Number(item.max_tool_rounds))
    const [generationModelId, setGenerationModelId] = useState(
      Number(item.generation_model_id || 0),
    )
    const [automaticKnowledgeEnabled, setAutomaticKnowledgeEnabled] = useState(
      item.automatic_knowledge_enabled !== false,
    )
    const [embeddingEnabled, setEmbeddingEnabled] = useState(Boolean(item.embedding_enabled))
    const [embeddingModelId, setEmbeddingModelId] = useState(Number(item.embedding_model_id || 0))
    const [confirmationMode, setConfirmationMode] = useState<ConfirmationMode>(
      existingConfirm.includes('*') ? 'all' : existingConfirm.length ? 'selected' : 'none',
    )
    const [confirmedTools, setConfirmedTools] = useState(
      new Set(existingConfirm.filter((tool) => tool !== '*')),
    )
    const [selectedSkills, setSelectedSkills] = useState(
      new Set((item.skill_ids || []).map(Number)),
    )
    const [confirmEmbedding, setConfirmEmbedding] = useState(false)
    const [validationError, setValidationError] = useState('')
    const [knowledgeServiceAgent, setKnowledgeServiceAgent] = useState<BuiltinAgent | null>(null)
    const [knowledgeServiceAction, setKnowledgeServiceAction] = useState<'setup' | 'test' | null>(
      null,
    )
    const [knowledgeServiceError, setKnowledgeServiceError] = useState('')
    const currentKnowledge = knowledgeServiceAgent || item
    const availableTools =
      (item.key === 'knowledge' ? currentKnowledge.tools : item.tools) || []
    const selectedModel = models.find((model) => Number(model.id) === modelId)
    const selectedGenerationModel = models.find(
      (model) => Number(model.id) === generationModelId,
    )
    const selectedEmbeddingModel = embeddingModels.find(
      (model) => Number(model.id) === embeddingModelId,
    )
    const embeddingDirty =
      item.key === 'knowledge' &&
      (embeddingEnabled !== Boolean(item.embedding_enabled) ||
        embeddingModelId !== Number(item.embedding_model_id || 0))
    const nextConfirmTools =
      confirmationMode === 'all'
        ? ['*']
        : confirmationMode === 'selected'
          ? [...confirmedTools].sort()
          : []
    const originalConfirmTools = [...existingConfirm].sort()
    const confirmationDirty =
      item.key !== 'computer' &&
      JSON.stringify(nextConfirmTools) !== JSON.stringify(originalConfirmTools)
    const nextSkillIds = [...selectedSkills].sort((left, right) => left - right)
    const visibleSkills = readOnly
      ? skills.filter((skill) => selectedSkills.has(Number(skill.id)))
      : skills
    const originalSkillIds = [...(item.skill_ids || [])]
      .map(Number)
      .sort((left, right) => left - right)
    const skillsDirty = JSON.stringify(nextSkillIds) !== JSON.stringify(originalSkillIds)
    const dirty =
      modelId !== Number(item.model_id || 0) ||
      maxToolRounds !== Number(item.max_tool_rounds) ||
      (item.key === 'media' && generationModelId !== Number(item.generation_model_id || 0)) ||
      (item.key === 'knowledge' &&
        automaticKnowledgeEnabled !== (item.automatic_knowledge_enabled !== false)) ||
      embeddingDirty ||
      confirmationDirty ||
      skillsDirty

    const saveChanges = () =>
      onSave?.({
        model_id: modelId || null,
        max_tool_rounds: maxToolRounds,
        ...(item.key === 'media' ? { generation_model_id: generationModelId || null } : {}),
        ...(item.key === 'knowledge'
          ? {
              automatic_knowledge_enabled: automaticKnowledgeEnabled,
              embedding_enabled: embeddingEnabled,
              embedding_model_id: embeddingModelId || null,
            }
          : {}),
        ...(item.key === 'computer' ? {} : { confirm_tools: nextConfirmTools }),
        skill_ids: nextSkillIds,
      }) || Promise.resolve()

    const requestSave = () => {
      setValidationError('')
      if (!Number.isInteger(maxToolRounds) || maxToolRounds < 1 || maxToolRounds > 100) {
        setPage('developer')
        setValidationError('Maximum tool rounds must be a whole number between 1 and 100.')
        return
      }
      if (item.key === 'knowledge' && embeddingEnabled && !embeddingModelId) {
        setValidationError('Choose a tested embedding model before enabling embeddings.')
        return
      }
      if (item.key !== 'computer' && confirmationMode === 'selected' && !confirmedTools.size) {
        setPage('security')
        setValidationError('Select at least one action requiring confirmation.')
        return
      }
      if (
        embeddingDirty &&
        embeddingEnabled &&
        (embeddingEnabled !== Boolean(item.embedding_enabled) ||
          embeddingModelId !== Number(item.embedding_model_id || 0))
      ) {
        setConfirmEmbedding(true)
        return
      }
      void saveChanges()
    }

    const runKnowledgeServiceAction = async (action: 'setup' | 'test') => {
      setKnowledgeServiceAction(action)
      setKnowledgeServiceError('')
      try {
        const saved =
          action === 'setup'
            ? await api.builtins.setupKnowledge()
            : await api.builtins.testKnowledge()
        setKnowledgeServiceAgent(saved)
      } catch (cause) {
        setKnowledgeServiceError(
          cause instanceof Error ? cause.message : 'Could not update the Knowledge service.',
        )
      } finally {
        setKnowledgeServiceAction(null)
      }
    }

    useImperativeHandle(ref, () => ({ save: requestSave }))
    useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange])
    useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])
    useEffect(() => {
      if (!readOnly) return
      const savedConfirm = stringList(
        item.confirm_tools,
        item.tools?.filter((tool) => tool.requires_confirmation).map((tool) => tool.name) || [],
      )
      setModelId(Number(item.model_id || 0))
      setMaxToolRounds(Number(item.max_tool_rounds))
      setGenerationModelId(Number(item.generation_model_id || 0))
      setAutomaticKnowledgeEnabled(item.automatic_knowledge_enabled !== false)
      setEmbeddingEnabled(Boolean(item.embedding_enabled))
      setEmbeddingModelId(Number(item.embedding_model_id || 0))
      setConfirmationMode(
        savedConfirm.includes('*') ? 'all' : savedConfirm.length ? 'selected' : 'none',
      )
      setConfirmedTools(new Set(savedConfirm.filter((tool) => tool !== '*')))
      setSelectedSkills(new Set((item.skill_ids || []).map(Number)))
    }, [item, readOnly])

    return (
      <div className={compact ? 'builtin-agent-write-form' : 'stack'}>
        <section
          className={`card resource-workspace ${readOnly ? 'resource-workspace--readonly' : ''}`}
        >
          <div className={`setting-row ${readOnly ? 'readonly-setting-row' : ''}`}>
            <span>
              <strong>{item.connected ? 'Connected' : 'Disconnected'}</strong>
              <small>
                Disconnected built-ins disappear from Overview and cannot receive tasks.
              </small>
            </span>
            <label className="switch">
              <input
                type="checkbox"
                checked={item.connected}
                disabled={connecting || !onConnect}
                onChange={(event) => onConnect?.(event.target.checked)}
              />
              <span className="switch__track">
                <span />
              </span>
            </label>
          </div>
          <div className="card__body detail-grid resource-detail-body">
            <SectionTabs
              className="subagent-form-tabs builtin-agent-tabs"
              label="Built-in subagent configuration sections"
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
                  count: availableTools.length,
                },
                { id: 'security', label: 'Security', icon: <ShieldCheck size={14} /> },
                { id: 'developer', label: 'Developer', icon: <Code2 size={14} /> },
              ]}
              onChange={(value) => setPage(value as typeof page)}
            />
            {page === 'configuration' && (
              <>
                {readOnly ? (
                  <Detail
                    label="Model"
                    value={
                      selectedModel
                        ? `${selectedModel.name} — ${selectedModel.provider_name || selectedModel.provider} — ${selectedModel.model}`
                        : item.model || 'Installation fallback'
                    }
                  />
                ) : (
                  <div className="detail">
                    <dt>Model</dt>
                    <dd>
                      <select
                        value={modelId || ''}
                        onChange={(event) => setModelId(Number(event.target.value))}
                      >
                        <option value="">Installation fallback</option>
                        {models.map((model) => (
                          <option key={model.id} value={model.id}>
                            {model.name} — {model.provider_name || model.provider} — {model.model}
                          </option>
                        ))}
                      </select>
                      {item.key === 'computer' && (
                        <span className="technical-warning technical-warning--compact">
                          <TriangleAlert size={15} aria-hidden="true" />
                          <span>
                            <strong>Vision-capable model recommended</strong>
                            Smaller or text-focused models may struggle to locate and click visual
                            targets reliably.
                          </span>
                        </span>
                      )}
                    </dd>
                  </div>
                )}
                {item.key === 'media' && (
                  readOnly ? (
                    <Detail
                      label="Image generation model"
                      value={
                        selectedGenerationModel
                          ? `${selectedGenerationModel.name} — ${selectedGenerationModel.provider_name || selectedGenerationModel.provider} — ${selectedGenerationModel.model}`
                          : item.generation_model || 'Not configured'
                      }
                    />
                  ) : (
                    <div className="detail">
                      <dt>Image generation model</dt>
                      <dd>
                        <select
                          value={generationModelId || ''}
                          onChange={(event) => setGenerationModelId(Number(event.target.value))}
                        >
                          <option value="">Not configured</option>
                          {models.map((model) => (
                            <option key={model.id} value={model.id}>
                              {model.name} — {model.provider_name || model.provider} — {model.model}
                            </option>
                          ))}
                        </select>
                      </dd>
                    </div>
                  )
                )}
                {item.key === 'knowledge' && (
                  <>
                    <div className="detail detail--full knowledge-service-setting">
                      <div className="knowledge-service-setting__heading">
                        <span>
                          <strong>Local knowledge service</strong>
                          <small>
                            GBrain stores and retrieves Knowledge data for this subagent. Its setup
                            and connection are managed here.
                          </small>
                        </span>
                        <Status value={currentKnowledge.knowledge_service_status || 'untested'} />
                      </div>
                      {currentKnowledge.knowledge_service_status === 'connected' &&
                        !currentKnowledge.knowledge_protocol_compatible && (
                          <span className="field__error">
                            Missing tools:{' '}
                            {(currentKnowledge.knowledge_protocol_missing_tools || []).join(', ')}
                          </span>
                        )}
                      <Feedback
                        message={
                          knowledgeServiceError ||
                          currentKnowledge.knowledge_service_last_error ||
                          ''
                        }
                      />
                      {!readOnly && (
                        <div className="knowledge-service-setting__actions">
                          <Button
                            type="button"
                            icon={<Play size={13} />}
                            busy={knowledgeServiceAction === 'setup'}
                            disabled={knowledgeServiceAction !== null}
                            onClick={() => void runKnowledgeServiceAction('setup')}
                          >
                            Set up service
                          </Button>
                          <Button
                            type="button"
                            variant="primary"
                            icon={<RefreshCw size={13} />}
                            busy={knowledgeServiceAction === 'test'}
                            disabled={knowledgeServiceAction !== null}
                            onClick={() => void runKnowledgeServiceAction('test')}
                          >
                            Test connection
                          </Button>
                        </div>
                      )}
                    </div>
                    {readOnly ? (
                      <>
                        <Detail
                          label="Automatic knowledge"
                          value={automaticKnowledgeEnabled ? 'Enabled' : 'Disabled'}
                        />
                        <Detail label="Embeddings" value={embeddingEnabled ? 'Enabled' : 'Disabled'} />
                        {embeddingEnabled && (
                          <Detail
                            label="Embedding model"
                            value={
                              selectedEmbeddingModel
                                ? `${selectedEmbeddingModel.name} — ${selectedEmbeddingModel.provider_name || selectedEmbeddingModel.adapter} — ${selectedEmbeddingModel.model}`
                                : 'Not configured'
                            }
                          />
                        )}
                      </>
                    ) : (
                      <>
                        <div className="detail detail--full knowledge-embedding-setting">
                          <div className="setting-row">
                            <span>
                              <strong>Automatic knowledge</strong>
                              <small>
                                Add relevant saved knowledge to Mounir’s context before each user
                                request.
                              </small>
                              {currentKnowledge.knowledge_service_status === 'connected' &&
                                !currentKnowledge.automatic_knowledge_available && (
                                  <small className="field__error">
                                    Automatic knowledge is unavailable because this GBrain version
                                    does not provide automatic context.
                                  </small>
                                )}
                            </span>
                            <label className="switch">
                              <input
                                type="checkbox"
                                checked={automaticKnowledgeEnabled}
                                onChange={(event) =>
                                  setAutomaticKnowledgeEnabled(event.target.checked)
                                }
                              />
                              <span className="switch__track">
                                <span />
                              </span>
                            </label>
                          </div>
                        </div>
                        <div className="detail detail--full knowledge-embedding-setting">
                          <div className="setting-row">
                            <span>
                              <strong>Embeddings</strong>
                              <small>
                                Help GBrain find memories by meaning when the wording differs. When
                                disabled, memories are still stored and searched using words and
                                text matching.
                              </small>
                            </span>
                            <label className="switch">
                              <input
                                type="checkbox"
                                checked={embeddingEnabled}
                                onChange={(event) => setEmbeddingEnabled(event.target.checked)}
                              />
                              <span className="switch__track">
                                <span />
                              </span>
                            </label>
                          </div>
                          {embeddingEnabled && (
                            <label className="field knowledge-embedding-model">
                              <span className="field__label">Embedding model</span>
                              <span className="field__hint">
                                Select a saved model with a successful connection test. Changing it
                                re-indexes existing memories.
                              </span>
                              <select
                                value={embeddingModelId || ''}
                                onChange={(event) => setEmbeddingModelId(Number(event.target.value))}
                              >
                                <option value="">Choose an embedding model</option>
                                {embeddingModels.map((model) => (
                                  <option
                                    key={model.id}
                                    value={model.id}
                                    disabled={
                                      model.connection_status !== 'connected' &&
                                      model.id !== embeddingModelId
                                    }
                                  >
                                    {model.name} — {model.provider_name || model.adapter} —{' '}
                                    {model.model}
                                    {model.dimensions ? ` · ${model.dimensions}d` : ''}
                                    {model.connection_status !== 'connected'
                                      ? ` · ${model.connection_status}`
                                      : ''}
                                  </option>
                                ))}
                              </select>
                              {!embeddingModels.length && (
                                <span className="field__error">
                                  Add and test an embedding model from Models first.
                                </span>
                              )}
                            </label>
                          )}
                        </div>
                      </>
                    )}
                  </>
                )}
                <Detail label="Description" value={item.description} full />
                <Detail label="System prompt" value={item.system_prompt} full />
              </>
            )}
            {page === 'skills' && (
              <div className="detail detail--full subagent-wizard__page">
                <p className="subagent-wizard__section-description">
                  Select the installed skills this built-in subagent can discover and activate.
                </p>
                <SkillPicker
                  skills={visibleSkills}
                  selected={selectedSkills}
                  loading={skillsLoading}
                  error={skillsError}
                  readOnly={readOnly}
                  empty="No skills are connected to this built-in subagent."
                  onChange={readOnly ? undefined : setSelectedSkills}
                />
              </div>
            )}
            {page === 'tools' && (
              <div className="detail detail--full subagent-wizard__page">
                <p className="subagent-wizard__section-description">
                  These tools are built into this specialist and managed by Mounir. They are shown
                  here for reference and cannot be added, removed, or modified.
                </p>
                <ToolChoices
                  tools={availableTools}
                  readOnly
                  empty="No tools are currently available for this built-in specialist."
                />
              </div>
            )}
            {page === 'security' && (
              <div className="detail detail--full builtin-agent-security">
                {item.key === 'computer' ? (
                  <p className="subagent-wizard__section-description">
                    Computer asks once before each desktop-control session begins. After you approve
                    that scoped task, its mouse and keyboard actions continue without additional
                    confirmation prompts. Screenshots are sent to the selected model provider for
                    visual reasoning, so choose a vision-capable model and provider you trust.
                  </p>
                ) : (
                  <>
                    {readOnly ? (
                      <Detail
                        label="Action confirmation"
                        value={
                          confirmationMode === 'all'
                            ? 'Ask before every action'
                            : confirmationMode === 'selected'
                              ? 'Ask for selected actions'
                              : 'Run without confirmation'
                        }
                        full
                      />
                    ) : (
                      <Field
                        full
                        label="Action confirmation"
                        hint="Control which actions require your approval before they run."
                      >
                        <select
                          value={confirmationMode}
                          onChange={(event) =>
                            setConfirmationMode(event.target.value as ConfirmationMode)
                          }
                        >
                          <option value="all">Ask before every action</option>
                          <option value="selected">Ask for selected actions</option>
                          <option value="none">Run without confirmation</option>
                        </select>
                      </Field>
                    )}
                    {confirmationMode === 'selected' && (
                      <div className="key-value-editor">
                        <div className="key-value-editor__title">
                          <span>
                            <strong>Actions requiring confirmation</strong>
                            <small>Select the actions Mounir must ask you to approve.</small>
                          </span>
                        </div>
                        <ToolChoices
                          tools={availableTools}
                          selected={confirmedTools}
                          readOnly={readOnly}
                          onChange={readOnly ? undefined : setConfirmedTools}
                          empty="Connect the built-in service to discover its available tools."
                        />
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
            {page === 'developer' && (
              <div className="detail detail--full developer-settings">
                {readOnly ? (
                  <Detail label="Maximum tool rounds" value={String(maxToolRounds)} />
                ) : (
                  <>
                    <div className="technical-warning">
                      <TriangleAlert size={17} aria-hidden="true" />
                      <span>
                        <strong>Technical settings</strong>
                        These limits are intended for developers and technical users. Leave the
                        default value unchanged unless you understand its effect on agent execution.
                      </span>
                    </div>
                    <Field
                      label="Maximum tool rounds"
                      hint="Maximum model-and-tool cycles allowed for one request (1–100)."
                    >
                      <input
                        type="number"
                        min={1}
                        max={100}
                        step={1}
                        value={maxToolRounds}
                        onChange={(event) => setMaxToolRounds(Number(event.target.value))}
                      />
                    </Field>
                    <div className="developer-settings__actions">
                    <Button
                      type="button"
                      icon={<RotateCcw size={14} />}
                      onClick={() => setMaxToolRounds(Number(item.default_max_tool_rounds))}
                    >
                      Reset to defaults
                    </Button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </section>
        <Feedback message={validationError || error || ''} />
        {!readOnly && (
          <ConfirmDialog
            open={confirmEmbedding}
            title="Update embeddings?"
            message="GBrain will configure this embedding model and index existing memories. This can take time, and a cloud provider may charge for the embedding requests."
            confirmLabel="Apply and index"
            busy={saving}
            error={error || ''}
            onConfirm={() => {
              void saveChanges()
                .then(() => setConfirmEmbedding(false))
                .catch(() => undefined)
            }}
            onCancel={() => setConfirmEmbedding(false)}
          />
        )}
      </div>
    )
  },
)
