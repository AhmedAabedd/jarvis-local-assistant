import { useState, type FormEvent } from 'react'
import type { McpServer } from '../../api/types'
import { AutoTextarea } from '../../components/ui/AutoTextarea'
import { Field } from '../../components/ui/Field'
import { Feedback } from '../../components/ui/Feedback'
import { entriesObject, KeyValueEditor, type Entry } from './KeyValueEditor'
import { objectValue, remoteAuth } from './helpers'

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
  const [transport, setTransport] = useState(item?.transport || 'stdio')
  const [authMode, setAuthMode] = useState(auth.mode)
  const [authMethod, setAuthMethod] = useState(auth.method)
  const [secret, setSecret] = useState(auth.secret)
  const [headerName, setHeaderName] = useState(auth.header)
  const [environment, setEnvironment] = useState<Entry[]>(
    Object.entries(initialEnvironment).map(([key, value]) => ({ key, value })),
  )
  const [headers, setHeaders] = useState<Entry[]>(
    Object.entries(initialHeaders).map(([key, value]) => ({ key, value })),
  )
  const [error, setError] = useState('')
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const body = Object.fromEntries(new FormData(event.currentTarget).entries()) as Record<
      string,
      unknown
    >
    try {
      if (transport === 'stdio') {
        body.env = JSON.stringify(
          entriesObject(environment, 'Credential', new Set(Object.keys(initialEnvironment))),
        )
        body.headers = '{}'
        body.auth_scheme = ''
      } else {
        body.env = '{}'
        let resolved: Record<string, string> = {}
        body.auth_scheme = authMode
        if (authMode === 'credential') {
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
        } else if (authMode === 'custom')
          resolved = entriesObject(headers, 'Header', new Set(Object.keys(initialHeaders)))
        body.headers = JSON.stringify(resolved)
      }
      await onSubmit(body)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the server.')
    }
  }
  return (
    <form id={formId} className="form-grid" onSubmit={submit}>
      <Field full label="Name">
        <input name="name" defaultValue={item?.name} required placeholder="Server name" />
      </Field>
      <Field full label="Description" hint="What this connection adds to the assistant.">
        <AutoTextarea name="description" defaultValue={item?.description} rows={3} />
      </Field>
      <Field full label="Connection type" hint="Use the transport documented by the MCP server.">
        <select
          name="transport"
          value={transport}
          onChange={(e) => setTransport(e.target.value as McpServer['transport'])}
        >
          <option value="stdio">Local server (stdio)</option>
          <option value="streamable_http">Remote server (HTTP)</option>
          <option value="sse">Remote server (legacy SSE)</option>
        </select>
      </Field>
      <Field
        full
        label="Connection"
        hint={
          transport === 'stdio'
            ? 'Paste the command used to start the server.'
            : 'Paste the MCP endpoint URL.'
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
      {transport === 'stdio' ? (
        <KeyValueEditor
          title="Local server credentials"
          entries={environment}
          onChange={setEnvironment}
        />
      ) : (
        <>
          <Field full label="Authentication">
            <select value={authMode} onChange={(e) => setAuthMode(e.target.value)}>
              <option value="none">No authentication</option>
              <option value="credential">Access token or API key</option>
              <option value="custom">Advanced custom headers</option>
            </select>
          </Field>
          {authMode === 'credential' && (
            <>
              <Field label="Authentication method">
                <select value={authMethod} onChange={(e) => setAuthMethod(e.target.value)}>
                  <option value="bearer">Bearer token</option>
                  <option value="header">Named API key header</option>
                </select>
              </Field>
              {authMethod === 'header' && (
                <Field label="Header name">
                  <input value={headerName} onChange={(e) => setHeaderName(e.target.value)} />
                </Field>
              )}
              <Field full label="Token or API key">
                <input
                  type="password"
                  value={secret}
                  placeholder={item?.headers_configured ? 'Saved — enter a replacement' : ''}
                  onChange={(e) => setSecret(e.target.value)}
                />
              </Field>
            </>
          )}
          {authMode === 'custom' && (
            <KeyValueEditor title="Custom headers" entries={headers} onChange={setHeaders} />
          )}
        </>
      )}
      <div className="field--full">
        <Feedback message={error} />
      </div>
    </form>
  )
}
