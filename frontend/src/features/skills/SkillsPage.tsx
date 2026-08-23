import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  BadgeCheck,
  BookOpen,
  CalendarDays,
  Download,
  ExternalLink,
  FileArchive,
  Files,
  FolderOpen,
  History,
  MessageCircle,
  PackageOpen,
  Search,
  Star,
  Store,
  Tag,
  Trash2,
  Upload,
  UserRound,
  Users,
} from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { SkillAssignment, SkillRecord, StoreSkill } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Loading } from '../../components/ui/Loading'
import { Modal } from '../../components/ui/Modal'
import { SectionTabs } from '../../components/ui/SectionTabs'
import { keys, useSkills, useSkillTargets } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'

type Page = 'installed' | 'store' | 'import'

const pageCopy: Record<Page, { label: string }> = {
  installed: {
    label: 'Installed',
  },
  store: {
    label: 'Skill Store',
  },
  import: {
    label: 'Import',
  },
}

function targetId(target: SkillAssignment) {
  return `${target.agent_type}:${target.agent_key}`
}

function storeDate(value?: number | string | null) {
  if (!value) return ''
  const date = new Date(typeof value === 'number' ? value : value)
  return Number.isNaN(date.getTime())
    ? ''
    : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

function storeReference(skill: StoreSkill) {
  return String(skill.reference || skill.slug).trim()
}

function InstalledSkills({ onOpen }: { onOpen: (skill: SkillRecord) => void }) {
  const skills = useSkills()
  const [search, setSearch] = useState('')
  const listed = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (skills.data || []).filter(
      (skill) =>
        !term ||
        skill.name.toLowerCase().includes(term) ||
        skill.description.toLowerCase().includes(term),
    )
  }, [search, skills.data])

  if (skills.isLoading) return <Loading label="Loading installed skills…" />
  if (skills.error) return <Feedback message={(skills.error as Error).message} />
  if (!skills.data?.length)
    return (
      <div className="card empty-state skills-empty">
        <BookOpen size={20} />
        <span>No skills installed yet. Open the Skill Store or import one.</span>
      </div>
    )
  return (
    <div className="resource-browser">
      <label className="resource-search">
        <Search size={13} />
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search installed skills…"
          aria-label="Search installed skills"
        />
      </label>
      {listed.length ? (
        <div className="resource-list">
          {listed.map((skill) => (
            <button
              type="button"
              className="resource-row resource-row--skills resource-row--without-status"
              key={skill.id}
              onClick={() => onOpen(skill)}
            >
              <span className="resource-row__identity">
                <span>
                  <strong>{skill.name}</strong>
                  <small>{skill.description}</small>
                </span>
              </span>
              <span className="resource-row__facts">
                <span title="Assigned agents">
                  <Users size={12} /> {skill.assignment_count} assigned
                </span>
                <span title="Package files">
                  <Files size={12} /> {skill.file_count} {skill.file_count === 1 ? 'file' : 'files'}
                </span>
                <span className="skills-source-label">
                  <PackageOpen size={12} />
                  {skill.source_type === 'import'
                    ? 'Imported'
                    : skill.source_name || skill.source_type}
                </span>
                {skill.version && (
                  <span>
                    <Tag size={12} /> v{skill.version}
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="card empty-state resource-search-empty">
          No installed skills match “{search.trim()}”.
        </div>
      )}
    </div>
  )
}

function SkillDetails({ skill, onBack }: { skill: SkillRecord; onBack: () => void }) {
  const client = useQueryClient()
  const targets = useSkillTargets()
  const [deleting, setDeleting] = useState(false)
  const remove = useMutation({
    mutationFn: () => api.skills.remove(skill.id),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: keys.skills })
      onBack()
    },
  })
  const groups = useMemo(() => {
    const assigned = new Set(skill.assignments.map(targetId))
    const result = new Map<string, NonNullable<typeof targets.data>>()
    for (const target of (targets.data || []).filter((item) => assigned.has(targetId(item)))) {
      const group = result.get(target.group) || []
      group.push(target)
      result.set(target.group, group)
    }
    return result
  }, [skill.assignments, targets.data])

  return (
    <>
      <section className="skills-detail">
        <div className="skills-detail__toolbar">
          <Button icon={<ArrowLeft size={15} />} onClick={onBack} aria-label="Back" title="Back" />
          <Button
            icon={<Trash2 size={15} />}
            onClick={() => setDeleting(true)}
            aria-label="Delete skill"
            title="Delete skill"
          />
        </div>
        <div className="card skills-detail__card">
          <div className="card__header">
            <div>
              <h3>{skill.name}</h3>
              <p>{skill.description}</p>
            </div>
            {skill.source_url && (
              <a
                className="button button--secondary"
                href={skill.source_url}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink size={13} /> Source
              </a>
            )}
          </div>
          <div className="card__body skills-detail__body">
            <dl className="detail-grid skills-detail__facts">
              <div className="detail">
                <dt>Source</dt>
                <dd>
                  {skill.source_type === 'import'
                    ? 'Imported package'
                    : skill.source_name || skill.source_type}
                </dd>
              </div>
              <div className="detail">
                <dt>Version</dt>
                <dd>{skill.version || 'Not specified'}</dd>
              </div>
              <div className="detail">
                <dt>Package</dt>
                <dd>
                  {skill.file_count} {skill.file_count === 1 ? 'file' : 'files'}
                </dd>
              </div>
              <div className="detail">
                <dt>Runtime support</dt>
                <dd>SKILL.md instructions</dd>
              </div>
            </dl>
            {skill.has_supporting_files && (
              <Feedback
                kind="info"
                message="This package includes supporting files. They are stored with the skill, but this version of Mounir reads only SKILL.md and does not execute package scripts."
              />
            )}
            <details className="skills-instructions">
              <summary>SKILL.md</summary>
              <pre>{skill.skill_md}</pre>
            </details>
            <section className="skills-assignments">
              <div>
                <h3>Used by</h3>
                <p>Agents that can discover and activate this skill.</p>
              </div>
              {targets.isLoading ? (
                <Loading label="Loading agents…" />
              ) : targets.error instanceof Error ? (
                <Feedback message={targets.error.message} />
              ) : groups.size ? (
                <div className="skills-target-groups">
                  {[...groups.entries()].map(([group, items]) => (
                    <fieldset key={group}>
                      <legend>{group}</legend>
                      {items.map((target) => (
                        <div
                          key={targetId(target)}
                          className="skills-target skills-target--readonly"
                        >
                          <Users size={14} />
                          <span>{target.name}</span>
                        </div>
                      ))}
                    </fieldset>
                  ))}
                </div>
              ) : (
                <p className="empty-inline">No agents currently use this skill.</p>
              )}
            </section>
          </div>
        </div>
      </section>
      <ConfirmDialog
        open={deleting}
        title="Delete skill?"
        message={`Permanently delete “${skill.name}”? It will be removed from every assigned agent.`}
        confirmLabel="Delete"
        danger
        busy={remove.isPending}
        error={remove.error instanceof Error ? remove.error.message : ''}
        onConfirm={() => remove.mutate()}
        onCancel={() => setDeleting(false)}
      />
    </>
  )
}

function SkillStore({ onInstalled }: { onInstalled: (skill: SkillRecord) => void }) {
  const client = useQueryClient()
  const installed = useSkills()
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [providerId, setProviderId] = useState('')
  const [selected, setSelected] = useState<StoreSkill | null>(null)
  const providers = useQuery({
    queryKey: ['skill-store-providers'],
    queryFn: api.skillStore.providers,
  })
  const provider = providers.data?.find((item) => item.id === providerId) || providers.data?.[0]
  const catalog = useInfiniteQuery({
    queryKey: ['skill-store', provider?.id, query],
    queryFn: ({ pageParam }) => api.skillStore.browse(provider!.id, query, pageParam),
    enabled: Boolean(provider),
    initialPageParam: '',
    getNextPageParam: (lastPage) => lastPage.next_cursor || undefined,
  })
  const detail = useQuery({
    queryKey: ['skill-store-detail', selected?.provider, selected && storeReference(selected)],
    queryFn: () => api.skillStore.details(selected!.provider, storeReference(selected!)),
    enabled: Boolean(selected),
  })
  const selectedSkill = selected
    ? {
        ...selected,
        ...detail.data,
        installability: detail.data?.installability || selected.installability,
        visibility: detail.data?.visibility || selected.visibility,
      }
    : null
  const install = useMutation({
    mutationFn: (skill: StoreSkill) =>
      api.skillStore.install(skill.provider, storeReference(skill), skill.version),
    onSuccess: async (skill) => {
      await client.invalidateQueries({ queryKey: keys.skills })
      setSelected(null)
      onInstalled(skill)
    },
  })
  const installedRefs = new Set(
    (installed.data || []).map((skill) => `${skill.source_type}:${skill.source_ref}`),
  )
  const storeItems = catalog.data?.pages.flatMap((page) => page.items) || []

  return (
    <div className="skill-store">
      <form
        className="skill-store__search"
        onSubmit={(event) => {
          event.preventDefault()
          setQuery(draft.trim())
        }}
      >
        {(providers.data?.length || 0) > 1 && (
          <select
            className="skill-store__provider"
            value={provider?.id || ''}
            onChange={(event) => setProviderId(event.target.value)}
            aria-label="Skill store provider"
          >
            {providers.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        )}
        <label className="resource-search">
          <Search size={13} />
          <input
            type="search"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={`Search ${provider?.name || 'skills'}…`}
            aria-label="Search the skill store"
          />
        </label>
        <Button
          className="skill-store__search-button"
          type="submit"
          variant="primary"
          icon={<Search size={14} />}
          aria-label="Search skills"
          title="Search"
        />
      </form>
      <Feedback
        message={
          providers.error instanceof Error
            ? providers.error.message
            : catalog.error instanceof Error
              ? catalog.error.message
              : install.error instanceof Error
                ? install.error.message
                : ''
        }
      />
      {providers.isLoading || catalog.isLoading ? (
        <Loading label="Loading the skill store…" />
      ) : storeItems.length ? (
        <>
          <div className="resource-list skill-store__list">
            {storeItems.map((skill) => {
              const reference = storeReference(skill)
              const isInstalled = installedRefs.has(`${skill.provider}:${reference}`)
              const isInstalling =
                install.isPending &&
                install.variables !== undefined &&
                storeReference(install.variables) === reference
              return (
                <div
                  className="resource-row resource-row--skills resource-row--without-status skill-store-card"
                  key={`${skill.provider}:${reference}`}
                >
                  <button
                    type="button"
                    className="skill-store-card__open"
                    onClick={() => setSelected(skill)}
                    aria-label={`View ${skill.name}`}
                  >
                    <span className="resource-row__identity">
                      <span>
                        <strong>{skill.name}</strong>
                        <small>{skill.description || 'No description provided.'}</small>
                      </span>
                    </span>
                    <span className="resource-row__facts">
                      <span title="Publisher">
                        <UserRound size={12} /> {skill.owner || skill.provider_name}
                      </span>
                      <span title="Downloads">
                        <Download size={12} /> {skill.downloads.toLocaleString()}
                      </span>
                      <span title="Stars">
                        <Star size={12} /> {skill.stars.toLocaleString()}
                      </span>
                      {isInstalled && (
                        <span className="skills-installed-label">
                          <BadgeCheck size={12} /> Installed
                        </span>
                      )}
                    </span>
                  </button>
                  <Button
                    className="skill-store-card__install"
                    variant="secondary"
                    icon={isInstalled ? <BadgeCheck size={14} /> : <Download size={14} />}
                    busy={isInstalling}
                    disabled={isInstalled}
                    onClick={() => {
                      install.reset()
                      install.mutate(skill)
                    }}
                    aria-label={
                      isInstalled ? `${skill.name} is installed` : `Install ${skill.name}`
                    }
                    title={isInstalled ? 'Installed' : 'Install'}
                  />
                </div>
              )
            })}
          </div>
          {catalog.hasNextPage && (
            <div className="skill-store__more">
              <Button busy={catalog.isFetchingNextPage} onClick={() => catalog.fetchNextPage()}>
                Load more
              </Button>
            </div>
          )}
        </>
      ) : (
        <div className="card empty-state">No skills found.</div>
      )}
      <Modal
        open={Boolean(selected)}
        wide
        title={selectedSkill?.name || 'Skill'}
        description={selectedSkill?.owner ? `Published by ${selectedSkill.owner}` : 'Skill details'}
        onClose={() => {
          install.reset()
          setSelected(null)
        }}
        footer={
          selectedSkill && (
            <Button
              variant="primary"
              icon={<Download size={14} />}
              busy={install.isPending}
              disabled={installedRefs.has(
                `${selectedSkill.provider}:${storeReference(selectedSkill)}`,
              )}
              onClick={() => install.mutate(selectedSkill)}
            >
              {installedRefs.has(`${selectedSkill.provider}:${storeReference(selectedSkill)}`)
                ? 'Installed'
                : 'Install'}
            </Button>
          )
        }
      >
        <div className="skill-store-preview skills-detail__body">
          {detail.isLoading ? (
            <Loading label="Loading skill details…" />
          ) : (
            <p>{selectedSkill?.description || 'No description provided.'}</p>
          )}
          <Feedback message={detail.error instanceof Error ? detail.error.message : ''} />
          <dl className="detail-grid skills-detail__facts">
            <div className="detail">
              <dt>Source</dt>
              <dd>{selectedSkill?.provider_name}</dd>
            </div>
            <div className="detail">
              <dt>Publisher</dt>
              <dd>{selectedSkill?.owner || 'Not specified'}</dd>
            </div>
            <div className="detail">
              <dt>Version</dt>
              <dd>{selectedSkill?.version || 'Latest'}</dd>
            </div>
            <div className="detail">
              <dt>Visibility</dt>
              <dd>{selectedSkill?.visibility || 'Public'}</dd>
            </div>
            <div className="detail">
              <dt>Downloads</dt>
              <dd>{selectedSkill?.downloads.toLocaleString()}</dd>
            </div>
            <div className="detail">
              <dt>Stars</dt>
              <dd>{selectedSkill?.stars.toLocaleString()}</dd>
            </div>
            {(selectedSkill?.installs || 0) > 0 && (
              <div className="detail">
                <dt>Installs</dt>
                <dd>{(selectedSkill?.installs || 0).toLocaleString()}</dd>
              </div>
            )}
            {(selectedSkill?.versions || 0) > 0 && (
              <div className="detail">
                <dt>Versions</dt>
                <dd>{(selectedSkill?.versions || 0).toLocaleString()}</dd>
              </div>
            )}
            {selectedSkill?.license && (
              <div className="detail">
                <dt>License</dt>
                <dd>{selectedSkill.license}</dd>
              </div>
            )}
            {selectedSkill?.installability && (
              <div className="detail">
                <dt>Security</dt>
                <dd>{selectedSkill.installability}</dd>
              </div>
            )}
            <div className="detail">
              <dt>Updated</dt>
              <dd>{storeDate(selectedSkill?.updated_at) || 'Not specified'}</dd>
            </div>
          </dl>
          {Boolean(selectedSkill?.topics.length || selectedSkill?.categories.length) && (
            <div className="skill-store-preview__topics">
              {[...(selectedSkill?.categories || []), ...(selectedSkill?.topics || [])].map(
                (topic) => (
                  <span key={topic}>{topic}</span>
                ),
              )}
            </div>
          )}
          {selectedSkill?.changelog && (
            <div className="skill-store-preview__section">
              <h3>Latest changes</h3>
              <p>{selectedSkill.changelog}</p>
            </div>
          )}
          {Object.keys(selectedSkill?.permissions || {}).length > 0 && (
            <div className="skill-store-preview__section">
              <h3>Permissions</h3>
              <pre className="skill-store-preview__code">
                {JSON.stringify(selectedSkill?.permissions, null, 2)}
              </pre>
            </div>
          )}
          {Object.keys(selectedSkill?.dependencies || {}).length > 0 && (
            <div className="skill-store-preview__section">
              <h3>Dependencies</h3>
              <pre className="skill-store-preview__code">
                {JSON.stringify(selectedSkill?.dependencies, null, 2)}
              </pre>
            </div>
          )}
          {(selectedSkill?.scan_findings || []).length > 0 && (
            <div className="skill-store-preview__section">
              <h3>Security findings</h3>
              <div className="skill-store-preview__findings">
                {selectedSkill?.scan_findings.map((finding, index) => (
                  <div key={`${finding.stage || 'finding'}:${index}`}>
                    <strong>
                      {[finding.severity, finding.type].filter(Boolean).join(' · ') || 'Finding'}
                    </strong>
                    <p>{finding.description || 'No description provided.'}</p>
                    {finding.location && <small>{finding.location}</small>}
                  </div>
                ))}
              </div>
            </div>
          )}
          {selectedSkill?.skill_md && (
            <details className="skills-instructions" open>
              <summary>SKILL.md</summary>
              <pre>{selectedSkill.skill_md}</pre>
            </details>
          )}
          <div className="skill-store-preview__meta">
            {selectedSkill?.installability && (
              <span>
                <BadgeCheck size={13} /> {selectedSkill.installability}
              </span>
            )}
            {(selectedSkill?.comments || 0) > 0 && (
              <span>
                <MessageCircle size={13} /> {(selectedSkill?.comments || 0).toLocaleString()}{' '}
                comments
              </span>
            )}
            {(selectedSkill?.versions || 0) > 0 && (
              <span>
                <History size={13} /> {(selectedSkill?.versions || 0).toLocaleString()} releases
              </span>
            )}
            {storeDate(selectedSkill?.created_at) && (
              <span>
                <CalendarDays size={13} /> Published {storeDate(selectedSkill?.created_at)}
              </span>
            )}
          </div>
          {selectedSkill?.source_url && (
            <a
              className="skill-store-preview__source"
              href={selectedSkill.source_url}
              target="_blank"
              rel="noreferrer"
            >
              View on {selectedSkill.provider_name} <ExternalLink size={13} />
            </a>
          )}
          <Feedback message={install.error instanceof Error ? install.error.message : ''} />
        </div>
      </Modal>
    </div>
  )
}

function ImportSkill({ onImported }: { onImported: (skill: SkillRecord) => void }) {
  const client = useQueryClient()
  const zipInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [paths, setPaths] = useState<string[]>([])
  const upload = useMutation({
    mutationFn: () => api.skills.import(files, paths),
    onSuccess: async (skill) => {
      await client.invalidateQueries({ queryKey: keys.skills })
      onImported(skill)
    },
  })
  const select = (selected: File[]) => {
    setFiles(selected)
    setPaths(selected.map((file) => file.webkitRelativePath || file.name))
    upload.reset()
  }
  return (
    <section className="card skill-import">
      <div className="skill-import__intro">
        <Upload size={22} />
        <div>
          <h3>Import a skill package</h3>
          <p>
            Select a standard skill folder, a ZIP archive, or one SKILL.md file. The complete
            package is stored locally; runtime access is limited to SKILL.md.
          </p>
        </div>
      </div>
      <div className="skill-import__choices">
        <button type="button" onClick={() => folderInput.current?.click()}>
          <FolderOpen size={19} />
          <span>
            <strong>Choose folder</strong>
            <small>Import the complete skill directory</small>
          </span>
        </button>
        <button type="button" onClick={() => zipInput.current?.click()}>
          <FileArchive size={19} />
          <span>
            <strong>Choose file</strong>
            <small>Import a ZIP package or SKILL.md</small>
          </span>
        </button>
      </div>
      <input
        ref={folderInput}
        className="visually-hidden"
        type="file"
        multiple
        {...({ webkitdirectory: '' } as Record<string, string>)}
        onChange={(event) => select(Array.from(event.target.files || []))}
      />
      <input
        ref={zipInput}
        className="visually-hidden"
        type="file"
        accept=".zip,.md,text/markdown,application/zip"
        onChange={(event) => select(Array.from(event.target.files || []))}
      />
      {files.length > 0 && (
        <div className="skill-import__selection">
          <span>
            <Files size={14} /> {files.length} {files.length === 1 ? 'file' : 'files'} selected
          </span>
          <Button variant="primary" busy={upload.isPending} onClick={() => upload.mutate()}>
            Import skill
          </Button>
        </div>
      )}
      <Feedback message={upload.error instanceof Error ? upload.error.message : ''} />
    </section>
  )
}

export function SkillsPage() {
  const skills = useSkills()
  const [page, setPage] = useState<Page>('installed')
  const [selected, setSelected] = useState<SkillRecord | null>(null)
  const openInstalled = (skill: SkillRecord) => {
    setSelected(skill)
    setPage('installed')
  }
  return (
    <>
      <PageHeader
        title="Skills"
        description="Add reusable instructions to Mounir and your subagents"
      />
      <div className="page-content skills-page">
        {!selected && (
          <SectionTabs
            className="skills-tabs"
            label="Skills pages"
            value={page}
            options={[
              {
                id: 'installed',
                label: pageCopy.installed.label,
                icon: <PackageOpen size={14} />,
                count: skills.data?.length || 0,
              },
              {
                id: 'store',
                label: pageCopy.store.label,
                icon: <Store size={14} />,
              },
              {
                id: 'import',
                label: pageCopy.import.label,
                icon: <Upload size={14} />,
              },
            ]}
            onChange={(value) => setPage(value as Page)}
          />
        )}
        {selected ? (
          <SkillDetails skill={selected} onBack={() => setSelected(null)} />
        ) : page === 'installed' ? (
          <InstalledSkills onOpen={setSelected} />
        ) : page === 'store' ? (
          <SkillStore onInstalled={openInstalled} />
        ) : (
          <ImportSkill onImported={openInstalled} />
        )}
      </div>
    </>
  )
}
