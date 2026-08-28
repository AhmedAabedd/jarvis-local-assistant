import { BookOpen, Save, Wrench } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { SkillRecord, Supervisor } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { SectionTabs } from '../../components/ui/SectionTabs'
import { SkillPicker } from '../resources/SkillPicker'
import { ToolChoices } from '../resources/ToolChoices'

type WizardPage = 'skills' | 'tools'

export function SupervisorWizard({
  open,
  supervisor,
  modelId,
  skills,
  skillsLoading,
  skillsError,
  busy,
  error,
  onModelChange,
  onSave,
  onClose,
}: {
  open: boolean
  supervisor?: Supervisor
  modelId: number
  skills: SkillRecord[]
  skillsLoading?: boolean
  skillsError?: string
  busy?: boolean
  error?: string
  onModelChange: (modelId: number) => void
  onSave: (skillIds: number[]) => void
  onClose: () => void
}) {
  const [page, setPage] = useState<WizardPage>('skills')
  const [selectedSkills, setSelectedSkills] = useState<Set<number>>(new Set())

  useEffect(() => {
    if (!open) return
    setPage('skills')
    setSelectedSkills(new Set((supervisor?.skill_ids || []).map(Number)))
  }, [open, supervisor])

  const tools = useMemo(
    () =>
      (supervisor?.tools || []).filter(
        (tool) =>
          !tool.name.startsWith('delegate_to_') &&
          tool.name !== 'delegate' &&
          tool.name !== 'delegate_task',
      ),
    [supervisor?.tools],
  )
  const savedSkillIds = useMemo(
    () => [...(supervisor?.skill_ids || [])].map(Number).sort((left, right) => left - right),
    [supervisor?.skill_ids],
  )
  const selectedSkillIds = [...selectedSkills].sort((left, right) => left - right)
  const dirty =
    modelId !== Number(supervisor?.model_id || 0) ||
    JSON.stringify(selectedSkillIds) !== JSON.stringify(savedSkillIds)

  return (
    <Modal
      open={open}
      wide
      integrated
      className="modal--compact-write-form modal--subagent-write-form modal--supervisor-wizard"
      title={supervisor?.name || 'Mounir'}
      description={
        supervisor?.description ||
        'Understands your request, uses local computer tools, and coordinates the right specialist for focused work.'
      }
      onClose={onClose}
      headingActions={
        <Button
          type="button"
          className="modal-heading-save-button"
          variant="primary"
          icon={<Save size={15} />}
          busy={busy}
          disabled={!modelId || !dirty}
          aria-label="Save Mounir changes"
          title="Save changes"
          onClick={() => onSave(selectedSkillIds)}
        />
      }
    >
      <div className="supervisor-wizard">
        <Field label="Model" hint="Select the model that powers Mounir.">
          <select
            value={modelId || ''}
            onChange={(event) => onModelChange(Number(event.target.value))}
          >
            <option value="">Choose a model…</option>
            {(supervisor?.model_options || []).map((model) => (
              <option value={model.id} key={model.id}>
                {model.label}
              </option>
            ))}
          </select>
        </Field>

        <SectionTabs
          className="subagent-form-tabs"
          label="Mounir capabilities"
          value={page}
          options={[
            {
              id: 'skills',
              label: 'Skills',
              icon: <BookOpen size={14} />,
              count: selectedSkills.size,
            },
            { id: 'tools', label: 'Tools', icon: <Wrench size={14} />, count: tools.length },
          ]}
          onChange={(value) => setPage(value as WizardPage)}
        />

        {page === 'skills' && (
          <div className="subagent-wizard__page">
            <p className="subagent-wizard__section-description">
              Select the installed skills Mounir can discover and activate.
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
          <div className="subagent-wizard__page">
            <p className="subagent-wizard__section-description">
              Tools available directly to Mounir. Delegation tools are intentionally omitted.
            </p>
            <ToolChoices tools={tools} readOnly empty="No direct tools are available." />
          </div>
        )}
        <Feedback message={error || ''} />
      </div>
    </Modal>
  )
}
