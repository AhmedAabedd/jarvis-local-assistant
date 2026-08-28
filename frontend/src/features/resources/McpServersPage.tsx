import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BadgeCheck,
  Cable,
  ChevronLeft,
  Edit3,
  ExternalLink,
  PackageOpen,
  Plus,
  Search,
  Store,
  Tag,
  Trash2,
  Wrench,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { api } from '../../api/client'
import type {
  McpRegistryInstallOption,
  McpRegistryPublishedOption,
  McpRegistryServer,
  McpServer,
} from '../../api/types'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Feedback } from '../../components/ui/Feedback'
import { Loading } from '../../components/ui/Loading'
import { Modal } from '../../components/ui/Modal'
import { SectionTabs } from '../../components/ui/SectionTabs'
import { Status } from '../../components/ui/Status'
import { keys, useServers } from '../../hooks/useStudioData'
import { PageHeader } from '../studio/PageHeader'
import { ResourceDetails } from './ResourceDetails'
import { ServerForm } from './ServerForm'

type Page = 'installed' | 'registry' | 'manual'

const FORM_ID = 'mcp-server-form'

function transportLabel(transport: McpServer['transport']) {
  if (transport === 'stdio') return 'Local process'
  if (transport === 'streamable_http') return 'Streamable HTTP'
  return 'Server-sent events'
}

function registryDate(value?: string | null) {
  if (!value) return 'Not specified'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? 'Not specified'
    : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

function registryReference(server: McpRegistryServer) {
  return `${server.provider}:${server.reference}`
}

function registryStatus(status?: string) {
  const value = String(status || '')
  if (!value) return 'Not specified'
  return value.charAt(0).toLocaleUpperCase() + value.slice(1)
}

function PublishedOptionDetails({ option }: { option: McpRegistryPublishedOption }) {
  return (
    <dl className="mcp-store-option__details">
      <div>
        <dt>{option.kind === 'remote' ? 'Endpoint' : 'Package'}</dt>
        <dd className="mono">{option.address || 'Not specified'}</dd>
      </div>
      <div>
        <dt>Transport</dt>
        <dd>{option.transport || 'Not specified'}</dd>
      </div>
      {option.version && (
        <div>
          <dt>Package version</dt>
          <dd>{option.version}</dd>
        </div>
      )}
      {option.runtime && (
        <div>
          <dt>Runtime</dt>
          <dd>{option.runtime}</dd>
        </div>
      )}
      {option.registry && (
        <div>
          <dt>Package source</dt>
          <dd className="mono">{option.registry}</dd>
        </div>
      )}
      <div>
        <dt>Required configuration</dt>
        <dd>{option.requirements.length ? option.requirements.join(', ') : 'None published'}</dd>
      </div>
      {option.integrity_available && (
        <div>
          <dt>Package integrity</dt>
          <dd>SHA-256 information published</dd>
        </div>
      )}
    </dl>
  )
}

function InstalledServers({
  servers,
  onOpen,
}: {
  servers: McpServer[]
  onOpen: (server: McpServer) => void
}) {
  const [search, setSearch] = useState('')
  const listed = useMemo(() => {
    const term = search.trim().toLocaleLowerCase()
    return servers.filter(
      (server) =>
        !term ||
        [server.name, server.description, server.connection, transportLabel(server.transport)].some(
          (value) =>
            String(value || '')
              .toLocaleLowerCase()
              .includes(term),
        ),
    )
  }, [search, servers])

  return (
    <div className="resource-browser">
      <label className="resource-search">
        <Search size={13} />
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search installed servers…"
          aria-label="Search installed MCP servers"
        />
      </label>
      {listed.length ? (
        <div className="resource-list">
          {listed.map((server) => (
            <button
              type="button"
              className="resource-row resource-row--servers"
              key={server.id}
              onClick={() => onOpen(server)}
            >
              <span className="resource-row__identity">
                <span className="mcp-server-card__identity-copy">
                  <span className="mcp-server-card__title">
                    <strong>{server.name}</strong>
                  </span>
                  <small>{server.description || 'No description provided.'}</small>
                </span>
              </span>
              <Status value={server.connection_status || 'untested'} />
              <span className="resource-row__facts">
                <span title="Transport">
                  <Cable size={12} /> {transportLabel(server.transport)}
                </span>
                <span title="Available tools">
                  <Wrench size={12} />{' '}
                  {server.tool_count === undefined
                    ? 'Tools not loaded'
                    : `${server.tool_count} tools`}
                </span>
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="card empty-state resource-search-empty">
          {servers.length
            ? `No installed servers match “${search.trim()}”.`
            : 'No MCP servers installed yet. Browse the MCP Store or use Manual setup.'}
        </div>
      )}
    </div>
  )
}

function RegistryBrowser({
  installed,
  onConfigure,
  onManualSetup,
}: {
  installed: McpServer[]
  onConfigure: (server: McpRegistryServer, option: McpRegistryInstallOption) => void
  onManualSetup: (server: McpRegistryServer) => void
}) {
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<McpRegistryServer | null>(null)
  const catalog = useInfiniteQuery({
    queryKey: ['mcp-registry', query],
    queryFn: ({ pageParam }) => api.mcpRegistry.browse(query, pageParam),
    initialPageParam: '',
    getNextPageParam: (lastPage) => lastPage.next_cursor || undefined,
  })
  const detail = useQuery({
    queryKey: ['mcp-registry-detail', selected?.reference, selected?.version],
    queryFn: () => api.mcpRegistry.details(selected!.reference, selected!.version || 'latest'),
    enabled: Boolean(selected),
  })
  const selectedServer = selected ? { ...selected, ...detail.data } : null
  const isSelectedInstalled = Boolean(
    selectedServer &&
    installed.some(
      (server) =>
        server.source_type === 'registry' && server.source_ref === selectedServer.reference,
    ),
  )
  const items = catalog.data?.pages.flatMap((page) => page.items) || []

  return (
    <div className="skill-store mcp-registry">
      <form
        className="skill-store__search"
        onSubmit={(event) => {
          event.preventDefault()
          setQuery(draft.trim())
        }}
      >
        <label className="resource-search">
          <Search size={13} />
          <input
            type="search"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Search the MCP Store…"
            aria-label="Search the MCP Store"
          />
        </label>
        <Button
          className="skill-store__search-button"
          type="submit"
          variant="primary"
          icon={<Search size={14} />}
          aria-label="Search MCP servers"
          title="Search"
        />
      </form>
      <Feedback message={catalog.error instanceof Error ? catalog.error.message : ''} />
      {catalog.isLoading ? (
        <Loading label="Loading the MCP Store…" />
      ) : items.length ? (
        <>
          <div className="resource-list skill-store__list">
            {items.map((server) => {
              const isInstalled = installed.some(
                (item) => item.source_type === 'registry' && item.source_ref === server.reference,
              )
              return (
                <div
                  className="resource-row resource-row--servers skill-store-card"
                  key={registryReference(server)}
                >
                  <button
                    type="button"
                    className="skill-store-card__open"
                    onClick={() => setSelected(server)}
                    aria-label={`View ${server.name}`}
                  >
                    <span className="resource-row__identity">
                      <span>
                        <strong>{server.name}</strong>
                        <small>{server.description || 'No description provided.'}</small>
                      </span>
                    </span>
                    <span className="resource-row__facts">
                      <span title="MCP Store identifier">
                        <Store size={12} /> {server.reference}
                      </span>
                      <span title="Version">
                        <Tag size={12} /> {server.version || 'Latest'}
                      </span>
                      <span title="Connection options">
                        <Cable size={12} /> {server.published_options.length} options
                      </span>
                      {isInstalled && (
                        <span className="skills-installed-label">
                          <BadgeCheck size={12} /> Installed
                        </span>
                      )}
                    </span>
                  </button>
                  {isInstalled && (
                    <Button
                      className="skill-store-card__install"
                      variant="secondary"
                      icon={<BadgeCheck size={14} />}
                      disabled
                      aria-label={`${server.name} is installed`}
                      title="Installed"
                    />
                  )}
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
        <div className="card empty-state">No MCP servers found.</div>
      )}

      <Modal
        open={Boolean(selected)}
        wide
        title={selectedServer?.name || 'MCP server'}
        description={selectedServer?.reference || 'MCP Store details'}
        onClose={() => setSelected(null)}
      >
        <div className="skill-store-preview mcp-registry-preview">
          {detail.isLoading ? (
            <Loading label="Loading server details…" />
          ) : (
            <p>{selectedServer?.description || 'No description provided.'}</p>
          )}
          <Feedback message={detail.error instanceof Error ? detail.error.message : ''} />
          <dl className="detail-grid skills-detail__facts">
            <div className="detail">
              <dt>Store source</dt>
              <dd>{selectedServer?.provider_name}</dd>
            </div>
            <div className="detail">
              <dt>Version</dt>
              <dd>{selectedServer?.version || 'Latest'}</dd>
            </div>
            <div className="detail">
              <dt>Status</dt>
              <dd>{registryStatus(selectedServer?.status)}</dd>
            </div>
            <div className="detail">
              <dt>Latest version</dt>
              <dd>
                {selectedServer?.is_latest === null
                  ? 'Not specified'
                  : selectedServer?.is_latest
                    ? 'Yes'
                    : 'No'}
              </dd>
            </div>
            <div className="detail">
              <dt>Published</dt>
              <dd>{registryDate(selectedServer?.published_at)}</dd>
            </div>
            <div className="detail">
              <dt>Updated</dt>
              <dd>{registryDate(selectedServer?.updated_at)}</dd>
            </div>
            <div className="detail detail--full">
              <dt>MCP identifier</dt>
              <dd className="mono">{selectedServer?.reference}</dd>
            </div>
            {selectedServer?.status_message && (
              <div className="detail detail--full">
                <dt>Status notice</dt>
                <dd>{selectedServer.status_message}</dd>
              </div>
            )}
            {selectedServer?.status_changed_at && (
              <div className="detail">
                <dt>Status changed</dt>
                <dd>{registryDate(selectedServer.status_changed_at)}</dd>
              </div>
            )}
            {selectedServer?.repository_source && (
              <div className="detail">
                <dt>Repository host</dt>
                <dd>{selectedServer.repository_source}</dd>
              </div>
            )}
            {selectedServer?.repository_subfolder && (
              <div className="detail">
                <dt>Project folder</dt>
                <dd className="mono">{selectedServer.repository_subfolder}</dd>
              </div>
            )}
            {selectedServer?.publisher_contact && (
              <div className="detail detail--full">
                <dt>Publisher contact</dt>
                <dd>{selectedServer.publisher_contact}</dd>
              </div>
            )}
          </dl>
          <section className="mcp-registry-options">
            <div>
              <h3>Connection options</h3>
              <p>Review the ways published by the provider to connect this MCP server.</p>
            </div>
            {selectedServer?.published_options.length ? (
              <div className="mcp-registry-options__list">
                {selectedServer.published_options.map((published) => {
                  const option = selectedServer.install_options.find(
                    (candidate) => candidate.id === published.id,
                  )
                  return (
                    <div className="mcp-registry-option" key={published.id}>
                      <div className="mcp-store-option__content">
                        <strong>{published.label}</strong>
                        <PublishedOptionDetails option={published} />
                      </div>
                      <Button
                        variant={option ? 'primary' : 'secondary'}
                        icon={option ? <Plus size={14} /> : <Wrench size={14} />}
                        disabled={isSelectedInstalled}
                        onClick={() =>
                          option
                            ? onConfigure(selectedServer, option)
                            : onManualSetup(selectedServer)
                        }
                      >
                        {isSelectedInstalled ? 'Installed' : option ? 'Configure' : 'Manual setup'}
                      </Button>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="mcp-store-manual-option">
                <Feedback
                  kind="info"
                  message="The publisher did not provide a connection that Mounir can configure automatically."
                />
                <Button
                  variant="secondary"
                  icon={<Wrench size={14} />}
                  disabled={isSelectedInstalled}
                  onClick={() => selectedServer && onManualSetup(selectedServer)}
                >
                  {isSelectedInstalled ? 'Installed' : 'Manual setup'}
                </Button>
              </div>
            )}
          </section>
          <div className="mcp-registry-preview__links">
            {selectedServer?.repository_url && (
              <a href={selectedServer.repository_url} target="_blank" rel="noreferrer">
                Source repository <ExternalLink size={13} />
              </a>
            )}
            {selectedServer?.website_url && (
              <a href={selectedServer.website_url} target="_blank" rel="noreferrer">
                Publisher website <ExternalLink size={13} />
              </a>
            )}
            {selectedServer?.publisher_contact && (
              <a href={`mailto:${selectedServer.publisher_contact}`}>Contact publisher</a>
            )}
          </div>
        </div>
      </Modal>
    </div>
  )
}

function registryDraft(server: McpRegistryServer, option: McpRegistryInstallOption): McpServer {
  return {
    id: 0,
    name: server.name,
    description: server.description,
    transport: option.transport,
    connection: option.connection,
    headers: option.headers,
    env: option.env,
    auth_scheme: option.auth_scheme,
    setup_command: '',
    connection_status: 'untested',
    source_type: 'registry',
    source_name: server.provider_name,
    source_ref: server.reference,
    source_version: server.version,
    source_url: server.repository_url || server.website_url,
  }
}

function registryManualDraft(server: McpRegistryServer): McpServer {
  return {
    id: 0,
    name: server.name,
    description: server.description,
    transport: 'stdio',
    connection: '',
    headers: {},
    env: {},
    auth_scheme: '',
    setup_command: '',
    connection_status: 'untested',
    source_type: 'registry',
    source_name: server.provider_name,
    source_ref: server.reference,
    source_version: server.version,
    source_url: server.repository_url || server.website_url,
  }
}

export function McpServersPage() {
  const client = useQueryClient()
  const servers = useServers()
  const [page, setPage] = useState<Page>('installed')
  const [selected, setSelected] = useState<McpServer | null>(null)
  const [editing, setEditing] = useState(false)
  const [draftServer, setDraftServer] = useState<McpServer | undefined>()
  const [formVersion, setFormVersion] = useState(0)
  const [deleting, setDeleting] = useState(false)

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: keys.servers }),
      client.invalidateQueries({ queryKey: keys.overview }),
      client.invalidateQueries({ queryKey: keys.agents }),
    ])
  }
  const save = useMutation({
    mutationFn: (body: object) => {
      if (editing && selected) return api.servers.update(Number(selected.id), body)
      const source = draftServer?.source_type === 'registry' ? draftServer : undefined
      return api.servers.create({
        ...body,
        ...(source
          ? {
              source_type: source.source_type,
              source_name: source.source_name,
              source_ref: source.source_ref,
              source_version: source.source_version,
              source_url: source.source_url,
            }
          : {}),
      })
    },
    onSuccess: async (saved) => {
      await refresh()
      setSelected(saved)
      setEditing(false)
      setDraftServer(undefined)
      setPage('installed')
    },
  })
  const remove = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error('No MCP server is selected.')
      return api.servers.remove(Number(selected.id))
    },
    onSuccess: async () => {
      await refresh()
      setSelected(null)
      setDeleting(false)
    },
  })

  const openPage = (nextPage: Page) => {
    save.reset()
    setSelected(null)
    setEditing(false)
    setDraftServer(undefined)
    setPage(nextPage)
    if (nextPage === 'manual') setFormVersion((version) => version + 1)
  }
  const configureRegistry = (
    registryServer: McpRegistryServer,
    option: McpRegistryInstallOption,
  ) => {
    setDraftServer(registryDraft(registryServer, option))
    setSelected(null)
    setEditing(false)
    setPage('manual')
    setFormVersion((version) => version + 1)
  }
  const manuallyConfigureRegistry = (registryServer: McpRegistryServer) => {
    setDraftServer(registryManualDraft(registryServer))
    setSelected(null)
    setEditing(false)
    setPage('manual')
    setFormVersion((version) => version + 1)
  }
  const formItem = editing ? selected || undefined : draftServer
  const backToInstalled = () => {
    save.reset()
    setSelected(null)
    setEditing(false)
    setDraftServer(undefined)
    setPage('installed')
  }
  const closeWriteForm = () => {
    save.reset()
    if (editing) {
      setEditing(false)
      return
    }
    const returnPage = draftServer?.source_type === 'registry' ? 'registry' : 'installed'
    setDraftServer(undefined)
    setPage(returnPage)
  }
  const writing = editing || page === 'manual'

  return (
    <>
      <PageHeader
        title={selected ? selected.name : 'MCP Servers'}
        description={
          selected
            ? 'Installed MCP server configuration'
            : 'Manage services and tools connected through MCP'
        }
        leading={
          selected ? (
            <Button
              icon={<ChevronLeft size={15} />}
              onClick={backToInstalled}
              aria-label="Back to installed servers"
              title="Back to installed servers"
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
                aria-label="Delete MCP server"
                title="Delete MCP server"
              />
              <Button
                className="resource-header-action"
                variant="primary"
                icon={<Edit3 size={15} />}
                onClick={() => setEditing(true)}
                aria-label="Edit MCP server"
                title="Edit MCP server"
              />
            </div>
          ) : undefined
        }
      />
      <div className="page-content skills-page mcp-servers-page">
        {!selected && (
          <SectionTabs
            className="skills-tabs mcp-servers-tabs"
            label="MCP server pages"
            value={page}
            options={[
              {
                id: 'installed',
                label: 'Installed',
                icon: <PackageOpen size={14} />,
                count: servers.data?.length || 0,
              },
              { id: 'registry', label: 'MCP Store', icon: <Store size={14} /> },
              { id: 'manual', label: 'Manual setup', icon: <Plus size={14} /> },
            ]}
            onChange={(value) => openPage(value as Page)}
          />
        )}
        {servers.isLoading ? (
          <Loading label="Loading MCP servers…" />
        ) : servers.error instanceof Error ? (
          <Feedback message={servers.error.message} />
        ) : selected ? (
          <section className="resource-detail-page">
            <ResourceDetails kind="servers" item={selected} models={[]} skills={[]} />
          </section>
        ) : page === 'registry' ? (
          <RegistryBrowser
            installed={servers.data || []}
            onConfigure={configureRegistry}
            onManualSetup={manuallyConfigureRegistry}
          />
        ) : (
          <InstalledServers servers={servers.data || []} onOpen={setSelected} />
        )}
      </div>
      <Modal
        open={writing}
        wide
        integrated
        className="modal--compact-write-form modal--mcp-write-form"
        title={editing ? `Edit ${selected?.name || 'MCP server'}` : 'Add MCP server'}
        description={
          editing
            ? 'Update this server connection and its private configuration.'
            : 'Configure a reusable MCP server connection.'
        }
        onClose={closeWriteForm}
      >
        {draftServer?.source_type === 'registry' && !editing && (
          <div className="guidance mcp-registry-draft-source">
            <Store size={15} />
            <span>
              <strong>{draftServer.source_name}</strong>
              <small>
                Review the published configuration and provide any required private values before
                saving.
              </small>
            </span>
          </div>
        )}
        <div className="compact-write-modal-form">
          <ServerForm
            key={`${editing ? selected?.id : 'new'}:${formVersion}`}
            item={formItem}
            formId={FORM_ID}
            onSubmit={async (body) => {
              await save.mutateAsync(body)
            }}
          />
        </div>
        <Feedback message={save.error instanceof Error ? save.error.message : ''} />
        <div className="compact-form-actions">
          <Button variant="primary" type="submit" form={FORM_ID} busy={save.isPending}>
            {editing ? 'Save changes' : 'Add server'}
          </Button>
        </div>
      </Modal>
      <ConfirmDialog
        open={deleting}
        title="Delete MCP server?"
        message={`This permanently removes “${selected?.name || ''}”. Servers in use cannot be deleted.`}
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
