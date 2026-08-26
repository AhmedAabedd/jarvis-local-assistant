import { BookOpen, Settings2, ShieldCheck, Wrench } from 'lucide-react'
import { forwardRef, useEffect, useImperativeHandle, useState } from 'react'
import type { BuiltinAgent, EmbeddingModelRecord, ModelRecord, SkillRecord } from '../../api/types'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { SectionTabs } from '../../components/ui/SectionTabs'
import { stringList } from './helpers'
import { ToolChoices } from './ToolChoices'
import { SkillPicker } from './SkillPicker'

type DetailsPage = 'configuration' | 'skills' | 'tools' | 'security'
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
  onConnect: (connected: boolean) => void
  onDirtyChange?: (dirty: boolean) => void
  onSave: (body: {
    model_id: number | null
    generation_model_id?: number | null
    mcp_server_id?: number | null
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
    const mcpServerId = Number(item.mcp_server_id || 0)
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
      JSON.stringify(nextConfirmTools) !== JSON.stringify(originalConfirmTools)
    const nextSkillIds = [...selectedSkills].sort((left, right) => left - right)
    const originalSkillIds = [...(item.skill_ids || [])]
      .map(Number)
      .sort((left, right) => left - right)
    const skillsDirty = JSON.stringify(nextSkillIds) !== JSON.stringify(originalSkillIds)
    const dirty =
      modelId !== Number(item.model_id || 0) ||
      (item.key === 'media' && generationModelId !== Number(item.generation_model_id || 0)) ||
      (item.key === 'knowledge' &&
        automaticKnowledgeEnabled !== (item.automatic_knowledge_enabled !== false)) ||
      embeddingDirty ||
      confirmationDirty ||
      skillsDirty

    const saveChanges = () =>
      onSave({
        model_id: modelId || null,
        ...(item.key === 'media' ? { generation_model_id: generationModelId || null } : {}),
        ...(item.key === 'knowledge'
          ? {
              mcp_server_id: mcpServerId,
              automatic_knowledge_enabled: automaticKnowledgeEnabled,
              embedding_enabled: embeddingEnabled,
              embedding_model_id: embeddingModelId || null,
            }
          : {}),
        confirm_tools: nextConfirmTools,
        skill_ids: nextSkillIds,
      })

    const requestSave = () => {
      setValidationError('')
      if (item.key === 'knowledge' && embeddingEnabled && !embeddingModelId) {
        setValidationError('Choose a tested embedding model before enabling embeddings.')
        return
      }
      if (confirmationMode === 'selected' && !confirmedTools.size) {
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

    useImperativeHandle(ref, () => ({ save: requestSave }))
    useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange])
    useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])

    return (
      <div className="stack">
        <section className="card resource-workspace">
          <div className="setting-row">
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
                disabled={connecting}
                onChange={(event) => onConnect(event.target.checked)}
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
                  count: item.tools?.length || 0,
                },
                { id: 'security', label: 'Security', icon: <ShieldCheck size={14} /> },
              ]}
              onChange={(value) => setPage(value as typeof page)}
            />
            {page === 'configuration' && (
              <>
                <Detail label="Overview state" value={item.enabled ? 'Active' : 'Inactive'} />
                <div className="detail">
                  <dt>Analysis model</dt>
                  <dd>
                    <select
                      value={modelId || ''}
                      onChange={(event) => setModelId(Number(event.target.value))}
                    >
                      <option value="">Installation fallback</option>
                      {models.map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.name} — {model.provider}
                        </option>
                      ))}
                    </select>
                  </dd>
                </div>
                {item.key === 'media' && (
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
                            {model.name} — {model.provider}
                          </option>
                        ))}
                      </select>
                    </dd>
                  </div>
                )}
                {item.key === 'knowledge' && (
                  <>
                    <div className="detail detail--full">
                      <dt>MCP server</dt>
                      <dd>
                        <span className="detail__value-stack">
                          <span>{item.mcp_server_name || 'GBrain'}</span>
                          <small className="field__hint">
                            Built-in local MCP server permanently assigned to Knowledge.
                          </small>
                          {item.mcp_server_id && !item.knowledge_protocol_compatible && (
                            <small className="field__error">
                              {item.mcp_server_status !== 'connected'
                                ? 'GBrain is not connected yet. Open it in MCP Servers to see the setup error or test it again.'
                                : `Missing tools: ${(item.knowledge_protocol_missing_tools || []).join(', ')}`}
                            </small>
                          )}
                        </span>
                      </dd>
                    </div>
                    <div className="detail detail--full knowledge-embedding-setting">
                      <div className="setting-row">
                        <span>
                          <strong>Automatic knowledge</strong>
                          <small>
                            Add relevant saved knowledge to Mounir’s context before each user
                            request.
                          </small>
                          {item.mcp_server_status === 'connected' &&
                            !item.automatic_knowledge_available && (
                              <small className="field__error">
                                Automatic knowledge is unavailable because this GBrain version does
                                not provide automatic context.
                              </small>
                            )}
                        </span>
                        <label className="switch">
                          <input
                            type="checkbox"
                            checked={automaticKnowledgeEnabled}
                            onChange={(event) => setAutomaticKnowledgeEnabled(event.target.checked)}
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
                            disabled, memories are still stored and searched using words and text
                            matching.
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
                                {model.name} — {model.model}
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
                  skills={skills}
                  selected={selectedSkills}
                  loading={skillsLoading}
                  error={skillsError}
                  onChange={setSelectedSkills}
                />
              </div>
            )}
            {page === 'tools' && (
              <div className="detail detail--full subagent-wizard__page">
                <p className="subagent-wizard__section-description">
                  Tools supplied by this built-in specialist are fixed by its installed service.
                </p>
                <ToolChoices
                  tools={item.tools || []}
                  readOnly
                  empty="Connect the built-in service to discover its available tools."
                />
              </div>
            )}
            {page === 'security' && (
              <div className="detail detail--full builtin-agent-security">
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
                {confirmationMode === 'selected' && (
                  <div className="key-value-editor">
                    <div className="key-value-editor__title">
                      <span>
                        <strong>Actions requiring confirmation</strong>
                        <small>Select the actions Mounir must ask you to approve.</small>
                      </span>
                    </div>
                    <ToolChoices
                      tools={item.tools || []}
                      selected={confirmedTools}
                      onChange={setConfirmedTools}
                      empty="Connect the built-in service to discover its available tools."
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
        <Feedback message={validationError || error || ''} />
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
      </div>
    )
  },
)
