import type { ProviderRecord } from '../../api/types'
import { Field } from '../../components/ui/Field'

export interface ProviderConnectionValue {
  providerId: string
  baseUrlId: string
  apiKeyId: string
}

export function ProviderConnectionFields({
  providers,
  value,
  onChange,
  urlLabel = 'Base URL',
  urlHint = 'Choose one of the named endpoints configured for this provider.',
}: {
  providers: ProviderRecord[]
  value: ProviderConnectionValue
  onChange: (value: ProviderConnectionValue) => void
  urlLabel?: string
  urlHint?: string
}) {
  const selected = providers.find((provider) => String(provider.id) === value.providerId)
  const changeProvider = (providerId: string) => {
    const provider = providers.find((option) => String(option.id) === providerId)
    onChange({
      providerId,
      baseUrlId: provider?.base_urls[0]?.id ? String(provider.base_urls[0].id) : '',
      apiKeyId: '',
    })
  }

  return (
    <>
      <Field
        full
        label="Provider"
        hint={
          providers.length
            ? 'Select a reusable provider configured in Settings.'
            : 'Create a provider in Settings before saving this model.'
        }
      >
        <select
          name="provider_id"
          value={value.providerId}
          onChange={(event) => changeProvider(event.target.value)}
          required
        >
          <option value="">Select a provider</option>
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.name}
            </option>
          ))}
        </select>
      </Field>
      <Field full label={urlLabel} hint={urlHint}>
        <select
          name="provider_base_url_id"
          value={value.baseUrlId}
          onChange={(event) => onChange({ ...value, baseUrlId: event.target.value })}
          disabled={!selected?.base_urls.length}
          required
        >
          <option value="">{selected ? 'Select a base URL' : 'Select a provider first'}</option>
          {selected?.base_urls.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name} — {entry.value}
            </option>
          ))}
        </select>
        {selected && !selected.base_urls.length && (
          <span className="field__error">This provider has no base URLs configured.</span>
        )}
      </Field>
      <Field
        full
        label="API key"
        hint="Choose a named provider credential, or select no key for an unauthenticated endpoint."
      >
        <select
          name="provider_api_key_id"
          value={value.apiKeyId}
          onChange={(event) => onChange({ ...value, apiKeyId: event.target.value })}
          disabled={!selected}
        >
          <option value="">No API key</option>
          {selected?.api_keys.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name}
            </option>
          ))}
        </select>
      </Field>
    </>
  )
}
