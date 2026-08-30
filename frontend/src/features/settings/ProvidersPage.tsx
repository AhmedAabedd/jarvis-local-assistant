import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, Edit3, KeyRound, Link2, Plus, Search, Trash2 } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { ProviderRecord } from '../../api/types'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Field } from '../../components/ui/Field'
import { Loading } from '../../components/ui/Loading'
import { Modal } from '../../components/ui/Modal'
import { keys, useProviders } from '../../hooks/useStudioData'
import { KeyValueEditor, entriesObject, type Entry } from '../resources/KeyValueEditor'
import { PageHeader } from '../studio/PageHeader'

function normalizeEntries(entries: Entry[], label: string, secret = false) {
  const result: Array<{ id?: number; name: string; value: string }> = []
  const names = new Set<string>()
  for (const entry of entries) {
    const name = entry.key.trim()
    const value = entry.value.trim()
    if (!name && !value) continue
    if (!name) throw new Error(`${label} name is required.`)
    if (!value && !(secret && entry.id && entry.configured)) {
      throw new Error(`${label} value is required for “${name}”.`)
    }
    const folded = name.toLocaleLowerCase()
    if (names.has(folded)) throw new Error(`${label} “${name}” is duplicated.`)
    names.add(folded)
    result.push({ ...(entry.id ? { id: entry.id } : {}), name, value })
  }
  return result
}

function ProviderForm({
  item,
  formId,
  onSubmit,
}: {
  item?: ProviderRecord
  formId: string
  onSubmit: (body: object) => Promise<void>
}) {
  const [error, setError] = useState('')
  const [baseUrls, setBaseUrls] = useState<Entry[]>(
    () =>
      item?.base_urls.map((entry) => ({ id: entry.id, key: entry.name, value: entry.value })) || [],
  )
  const [apiKeys, setApiKeys] = useState<Entry[]>(
    () =>
      item?.api_keys.map((entry) => ({
        id: entry.id,
        key: entry.name,
        value: '',
        configured: entry.configured,
        locked: true,
        preview: entry.preview,
      })) || [],
  )
  const [headers, setHeaders] = useState<Entry[]>(() =>
    Object.entries(item?.headers || {}).map(([key, value]) => ({ key, value })),
  )

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const data = new FormData(event.currentTarget)
    try {
      await onSubmit({
        name: data.get('name'),
        description: data.get('description'),
        base_urls: normalizeEntries(baseUrls, 'Base URL'),
        api_keys: normalizeEntries(apiKeys, 'API key', true),
        headers: entriesObject(headers, 'Header'),
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save the provider.')
    }
  }

  return (
    <form id={formId} className="form-grid" onSubmit={submit}>
      <Field full label="Name" hint="A recognizable provider or local service name.">
        <input name="name" defaultValue={item?.name} required placeholder="Provider name" />
      </Field>
      <Field full label="Description" hint="Optional notes about this service or account.">
        <textarea
          name="description"
          defaultValue={item?.description}
          rows={3}
          placeholder="What this provider is used for"
        />
      </Field>
      <KeyValueEditor
        title="Base URLs"
        hint="Add every endpoint models may select, such as LLM or voice API roots."
        entries={baseUrls}
        onChange={setBaseUrls}
        secret={false}
        namePlaceholder="Endpoint name"
        valuePlaceholder="https://provider.example/v1"
      />
      <KeyValueEditor
        title="Default HTTP headers"
        hint="Optional metadata or routing headers shared by this provider. Environment references such as $SITE_URL are resolved at runtime."
        entries={headers}
        onChange={setHeaders}
        secret={false}
        namePlaceholder="Header name"
        valuePlaceholder="Header value"
      />
      <KeyValueEditor
        title="API keys"
        hint="Saved keys are locked. Remove one and add a new key to replace it."
        entries={apiKeys}
        onChange={setApiKeys}
        namePlaceholder="Credential name"
        valuePlaceholder="API key or environment reference"
      />
      <div className="field--full">
        <Feedback message={error} />
      </div>
    </form>
  )
}

export function ProvidersPage() {
  const query = useProviders()
  const client = useQueryClient()
  const [selected, setSelected] = useState<ProviderRecord | null>(null)
  const [editing, setEditing] = useState<ProviderRecord | null | undefined>(undefined)
  const [deleting, setDeleting] = useState(false)
  const [search, setSearch] = useState('')
  const formId = 'provider-form'

  const save = useMutation({
    mutationFn: (body: object) =>
      editing ? api.providers.update(editing.id, body) : api.providers.create(body),
    onSuccess: async (saved) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.providers }),
        client.invalidateQueries({ queryKey: keys.models }),
        client.invalidateQueries({ queryKey: keys.modelCatalog }),
        client.invalidateQueries({ queryKey: keys.embeddingModels }),
        client.invalidateQueries({ queryKey: keys.voiceModels }),
      ])
      setEditing(undefined)
      setSelected(saved)
    },
  })
  const remove = useMutation({
    mutationFn: () => {
      if (!selected) return Promise.resolve()
      return api.providers.remove(selected.id)
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: keys.providers })
      setSelected(null)
      setDeleting(false)
    },
  })
  const listed = useMemo(() => {
    const term = search.trim().toLocaleLowerCase()
    return (query.data || []).filter((provider) =>
      [
        provider.name,
        provider.description,
        ...provider.base_urls.flatMap((entry) => [entry.name, entry.value]),
        ...provider.api_keys.map((entry) => entry.name),
      ].some((value) => value.toLocaleLowerCase().includes(term)),
    )
  }, [query.data, search])

  return (
    <>
      <PageHeader
        title={selected?.name || 'Providers'}
        description={
          selected
            ? 'Reusable endpoints and credentials for model connections'
            : 'Manage reusable model endpoints and API keys'
        }
        leading={
          selected ? (
            <Button
              icon={<ChevronLeft size={15} />}
              onClick={() => setSelected(null)}
              aria-label="Back to providers"
              title="Back to providers"
            />
          ) : undefined
        }
        actions={
          selected ? (
            <div className="resource-header-actions">
              <Button
                className="resource-header-action"
                icon={<Trash2 size={15} />}
                onClick={() => setDeleting(true)}
                aria-label="Delete provider"
                title="Delete provider"
              />
              <Button
                className="resource-header-action"
                variant="primary"
                icon={<Edit3 size={15} />}
                onClick={() => setEditing(selected)}
                aria-label="Edit provider"
                title="Edit provider"
              />
            </div>
          ) : (
            <Button variant="primary" icon={<Plus size={15} />} onClick={() => setEditing(null)}>
              Add provider
            </Button>
          )
        }
      />
      <div className="page-content">
        {query.isLoading ? (
          <Loading />
        ) : query.error ? (
          <Feedback
            message={
              query.error instanceof Error ? query.error.message : 'Providers could not be loaded.'
            }
          />
        ) : selected ? (
          <section className="card resource-workspace provider-details">
            <div className="card__body detail-grid">
              <div className="detail detail--full">
                <dt>Description</dt>
                <dd>{selected.description || 'No description'}</dd>
              </div>
              <div className="detail detail--full">
                <dt>Base URLs</dt>
                <dd className="provider-detail-list">
                  {selected.base_urls.length ? (
                    selected.base_urls.map((entry) => (
                      <span key={entry.id}>
                        <strong>{entry.name}</strong>
                        <code>{entry.value}</code>
                      </span>
                    ))
                  ) : (
                    <span>None configured</span>
                  )}
                </dd>
              </div>
              <div className="detail detail--full">
                <dt>Default HTTP headers</dt>
                <dd className="provider-detail-list">
                  {Object.keys(selected.headers || {}).length ? (
                    Object.entries(selected.headers).map(([name, value]) => (
                      <span key={name}>
                        <strong>{name}</strong>
                        <code>{value}</code>
                      </span>
                    ))
                  ) : (
                    <span>None configured</span>
                  )}
                </dd>
              </div>
              <div className="detail detail--full">
                <dt>API keys</dt>
                <dd className="chips">
                  {selected.api_keys.length ? (
                    selected.api_keys.map((entry) => (
                      <span className="chip" key={entry.id}>
                        {entry.name}
                      </span>
                    ))
                  ) : (
                    <span className="chip">None</span>
                  )}
                </dd>
              </div>
            </div>
          </section>
        ) : (
          <div className="resource-browser">
            <label className="resource-search">
              <Search size={13} />
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search providers…"
                aria-label="Search providers"
              />
            </label>
            {listed.length ? (
              <div className="resource-list">
                {listed.map((provider) => (
                  <button
                    key={provider.id}
                    type="button"
                    className="resource-row resource-row--models resource-row--without-status"
                    onClick={() => setSelected(provider)}
                  >
                    <div className="resource-row__identity">
                      <span className="resource-row__identity-copy">
                        <strong>{provider.name}</strong>
                        <small>{provider.description || 'Reusable model provider'}</small>
                      </span>
                    </div>
                    <span className="resource-row__facts">
                      <span>
                        <Link2 size={12} /> {provider.base_urls.length} base URLs
                      </span>
                      <span>
                        <KeyRound size={12} /> {provider.api_keys.length} API keys
                      </span>
                      <span>{provider.model_count} models</span>
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="card empty-state resource-search-empty">
                {search.trim()
                  ? `No providers match “${search.trim()}”.`
                  : 'No providers saved yet.'}
              </div>
            )}
          </div>
        )}
      </div>
      <Modal
        open={editing !== undefined}
        wide
        integrated
        fixedInitialHeight
        className="modal--compact-write-form modal--provider-write-form"
        title={editing ? `Edit ${editing.name}` : 'Add provider'}
        description="Configure reusable endpoints and private credentials for your models."
        onClose={() => {
          save.reset()
          setEditing(undefined)
        }}
      >
        <div className="compact-write-modal-form provider-write-modal-form">
          <ProviderForm
            key={editing?.id || 'new-provider'}
            item={editing || undefined}
            formId={formId}
            onSubmit={async (body) => {
              await save.mutateAsync(body)
            }}
          />
        </div>
        <div className="compact-form-actions">
          <Button variant="primary" type="submit" form={formId} busy={save.isPending}>
            {editing ? 'Save changes' : 'Create provider'}
          </Button>
        </div>
      </Modal>
      <ConfirmDialog
        open={deleting}
        title="Delete provider?"
        message={`Permanently delete “${selected?.name || ''}”? Providers assigned to models cannot be deleted.`}
        confirmLabel="Delete"
        danger
        busy={remove.isPending}
        error={remove.error instanceof Error ? remove.error.message : ''}
        onConfirm={() => remove.mutate()}
        onCancel={() => {
          remove.reset()
          setDeleting(false)
        }}
      />
    </>
  )
}
