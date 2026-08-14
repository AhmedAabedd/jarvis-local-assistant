import { FileKey2, Plus, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import type { McpCredentialFile, McpServer } from '../../api/types'
import { AutoTextarea } from '../../components/ui/AutoTextarea'
import { Button } from '../../components/ui/Button'
import { Field } from '../../components/ui/Field'
import { Feedback } from '../../components/ui/Feedback'
import { entriesObject, KeyValueEditor, type Entry } from './KeyValueEditor'
import { objectValue, remoteAuth } from './helpers'

interface CredentialFileEntry extends McpCredentialFile {
  saved: boolean
  file?: File
}

const fileContent = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`))
    reader.readAsDataURL(file)
  })

export function ServerForm({
  item,
  formId,
  onSubmit,
}: {
  item?: McpServer
  formId: string
  onSubmit: (body: object) => Promise<void>
}) {
  const initialHeaders = objectValue(item?.headers)
  const initialEnvironment = objectValue(item?.env)
  const auth = remoteAuth(initialHeaders, item?.auth_scheme)
  const editableInitialHeaders = ['oauth', 'custom'].includes(auth.mode) ? initialHeaders : {}
  const [transport, setTransport] = useState(item?.transport || 'stdio')
  const [authMode, setAuthMode] = useState(auth.mode)
  const [authMethod, setAuthMethod] = useState(auth.method)
  const [secret, setSecret] = useState(auth.secret)
  const [headerName, setHeaderName] = useState(auth.header)
  const [environment, setEnvironment] = useState<Entry[]>(
    Object.entries(initialEnvironment).map(([key, value]) => ({ key, value })),
  )
  const [headers, setHeaders] = useState<Entry[]>(
    Object.entries(editableInitialHeaders).map(([key, value]) => ({ key, value })),
  )
  const [credentialFiles, setCredentialFiles] = useState<CredentialFileEntry[]>(
    (item?.credential_files || []).map((entry) => ({ ...entry, saved: true })),
  )
  const [removedFiles, setRemovedFiles] = useState<string[]>([])
  const [error, setError] = useState('')

  const removeFile = (index: number) => {
    const entry = credentialFiles[index]
    if (entry.saved) setRemovedFiles((values) => [...values, entry.env_var])
    setCredentialFiles((values) => values.filter((_, current) => current !== index))
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const body = Object.fromEntries(new FormData(event.currentTarget).entries()) as Record<
      string,
      unknown
    >
    try {
      body.transport = transport
      body.setup_command = transport === 'stdio' ? body.setup_command || '' : ''
      body.remove_credential_files = removedFiles
      body.credential_files = []
      if (transport === 'stdio') {
        const resolvedEnvironment = entriesObject(
          environment,
          'Environment variable',
          new Set(Object.keys(initialEnvironment)),
        )
        body.env = JSON.stringify(resolvedEnvironment)
        body.headers = '{}'
        body.auth_scheme = ''
        const names = new Set<string>()
        const uploads = []
        for (const entry of credentialFiles) {
          const envVar = entry.env_var.trim()
          if (!envVar) throw new Error('Environment variable is required for each credential file.')
          if (names.has(envVar)) throw new Error(`Credential file “${envVar}” is duplicated.`)
          if (Object.hasOwn(resolvedEnvironment, envVar))
            throw new Error(`Environment variable “${envVar}” is already configured.`)
          names.add(envVar)
          if (!entry.saved && !entry.file) throw new Error(`Choose a file for “${envVar}”.`)
          if (entry.file) {
            if (entry.file.size > 2 * 1024 * 1024)
              throw new Error('Each credential file must be 2 MB or smaller.')
            uploads.push({
              env_var: envVar,
              filename: entry.file.name,
              content: await fileContent(entry.file),
            })
          }
        }
        body.credential_files = uploads
      } else {
        body.env = '{}'
        body.remove_credential_files = (item?.credential_files || []).map((file) => file.env_var)
        let resolved: Record<string, string> = {}
        body.auth_scheme = authMode
        if (authMode === 'oauth') {
          body.headers = JSON.stringify(
            entriesObject(headers, 'Header', new Set(Object.keys(editableInitialHeaders))),
          )
        } else if (authMode === 'credential') {
          const sameSavedCredential =
            Boolean(item?.headers_configured) &&
            auth.mode === 'credential' &&
            auth.method === authMethod &&
            (authMethod === 'bearer' || auth.header === headerName.trim())
          if (!secret.trim() && !sameSavedCredential)
            throw new Error('Token or API key is required.')
          body.auth_scheme = authMethod
          if (authMethod === 'bearer')
            resolved.Authorization = secret.trim() ? `Bearer ${secret.trim()}` : ''
          else {
            if (!headerName.trim()) throw new Error('API key header name is required.')
            resolved[headerName.trim()] = secret
          }
          body.headers = JSON.stringify(resolved)
        } else if (authMode === 'custom') {
          resolved = entriesObject(headers, 'Header', new Set(Object.keys(editableInitialHeaders)))
          body.headers = JSON.stringify(resolved)
        } else body.headers = '{}'
      }
      await onSubmit(body)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the server.')
    }
  }

  return (
    <form id={formId} className="server-form" onSubmit={submit}>
      <section className="server-form-section">
        <div className="server-form-section__heading">
          <span>1</span>
          <div>
            <strong>Server details</strong>
            <small>Name this reusable connection so it is easy to recognize.</small>
          </div>
        </div>
        <div className="form-grid">
          <Field full label="Name">
            <input name="name" defaultValue={item?.name} required placeholder="Server name" />
          </Field>
          <Field full label="Description" hint="What this connection adds to Mounir.">
            <AutoTextarea name="description" defaultValue={item?.description} rows={3} />
          </Field>
        </div>
      </section>

      <section className="server-form-section">
        <div className="server-form-section__heading">
          <span>2</span>
          <div>
            <strong>Connection</strong>
            <small>Choose the transport published by the MCP server.</small>
          </div>
        </div>
        <div className="form-grid">
          <div className="field field--full">
            <span className="field__label">Transport</span>
            <div className="transport-choice-grid">
              {(
                [
                  ['stdio', 'Local', 'Run a command on this computer'],
                  ['streamable_http', 'Remote HTTP', 'Connect to a modern MCP endpoint'],
                  ['sse', 'Legacy SSE', 'Connect to an older SSE endpoint'],
                ] as const
              ).map(([value, label, hint]) => (
                <label className={transport === value ? 'is-selected' : ''} key={value}>
                  <input
                    type="radio"
                    name="transport_choice"
                    value={value}
                    checked={transport === value}
                    onChange={() => setTransport(value)}
                  />
                  <span>
                    <strong>{label}</strong>
                    <small>{hint}</small>
                  </span>
                </label>
              ))}
            </div>
          </div>
          <Field
            full
            label={transport === 'stdio' ? 'Start command' : 'Endpoint URL'}
            hint={
              transport === 'stdio'
                ? 'Enter the executable and arguments used to start the MCP server.'
                : 'Enter the complete URL published by the MCP server.'
            }
          >
            <input
              name="connection"
              defaultValue={item?.connection}
              required
              placeholder={
                transport === 'stdio' ? 'npx -y @example/mcp-server' : 'https://example.com/mcp'
              }
            />
          </Field>
        </div>
      </section>

      <section className="server-form-section">
        <div className="server-form-section__heading">
          <span>3</span>
          <div>
            <strong>{transport === 'stdio' ? 'Local access' : 'Authentication'}</strong>
            <small>
              {transport === 'stdio'
                ? 'Pass only the environment values and private files this server needs.'
                : 'Use the authentication method documented by the remote server.'}
            </small>
          </div>
        </div>
        <div className="form-grid">
          {transport === 'stdio' ? (
            <>
              <KeyValueEditor
                title="Environment variables"
                entries={environment}
                onChange={setEnvironment}
              />
              <div className="credential-file-editor">
                <div className="key-value-editor__title">
                  <div>
                    <strong>Credential files</strong>
                    <small>
                      Files stay private and their local paths are passed through your chosen
                      variable.
                    </small>
                  </div>
                  <Button
                    type="button"
                    icon={<Plus size={13} />}
                    onClick={() =>
                      setCredentialFiles((values) => [
                        ...values,
                        { env_var: '', filename: '', saved: false },
                      ])
                    }
                  >
                    Add file
                  </Button>
                </div>
                {!credentialFiles.length && (
                  <div className="credential-file-empty">
                    <FileKey2 size={17} />
                    <span>No credential files configured.</span>
                  </div>
                )}
                {credentialFiles.map((entry, index) => (
                  <div className="credential-file-row" key={`${entry.env_var}-${index}`}>
                    <input
                      aria-label="Credential environment variable"
                      placeholder="Environment variable"
                      value={entry.env_var}
                      disabled={entry.saved}
                      onChange={(event) =>
                        setCredentialFiles((values) =>
                          values.map((value, current) =>
                            current === index ? { ...value, env_var: event.target.value } : value,
                          ),
                        )
                      }
                    />
                    <label className="file-picker">
                      <span>{entry.file?.name || entry.filename || 'Choose file'}</span>
                      <input
                        type="file"
                        onChange={(event) => {
                          const file = event.target.files?.[0]
                          if (!file) return
                          setCredentialFiles((values) =>
                            values.map((value, current) =>
                              current === index ? { ...value, file, filename: file.name } : value,
                            ),
                          )
                        }}
                      />
                    </label>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="Remove credential file"
                      onClick={() => removeFile(index)}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
              <Field
                full
                label="Setup command"
                hint="Optional. Run this only when the server requires a separate initialization or authorization command."
              >
                <input
                  name="setup_command"
                  defaultValue={item?.setup_command}
                  placeholder="npx -y @example/mcp-server auth"
                />
              </Field>
            </>
          ) : (
            <>
              <Field full label="Authentication method">
                <select value={authMode} onChange={(event) => setAuthMode(event.target.value)}>
                  <option value="none">No authentication</option>
                  <option value="oauth">OAuth (automatic discovery)</option>
                  <option value="credential">Access token or API key</option>
                  <option value="custom">Advanced custom headers</option>
                </select>
              </Field>
              {authMode === 'oauth' && (
                <>
                  <div className="guidance">
                    Save the server, then connect your account from its setup section. Mounir uses
                    the MCP OAuth standard and discovers the provider endpoints automatically.
                  </div>
                  <KeyValueEditor
                    title="Additional headers"
                    entries={headers}
                    onChange={setHeaders}
                  />
                </>
              )}
              {authMode === 'credential' && (
                <>
                  <Field label="Credential type">
                    <select
                      value={authMethod}
                      onChange={(event) => setAuthMethod(event.target.value)}
                    >
                      <option value="bearer">Bearer token</option>
                      <option value="header">Named API key header</option>
                    </select>
                  </Field>
                  {authMethod === 'header' && (
                    <Field label="Header name">
                      <input
                        value={headerName}
                        onChange={(event) => setHeaderName(event.target.value)}
                      />
                    </Field>
                  )}
                  <Field full label="Token or API key">
                    <input
                      type="password"
                      value={secret}
                      placeholder={item?.headers_configured ? 'Saved — enter a replacement' : ''}
                      onChange={(event) => setSecret(event.target.value)}
                    />
                  </Field>
                </>
              )}
              {authMode === 'custom' && (
                <KeyValueEditor title="Custom headers" entries={headers} onChange={setHeaders} />
              )}
            </>
          )}
        </div>
      </section>
      <Feedback message={error} />
    </form>
  )
}
