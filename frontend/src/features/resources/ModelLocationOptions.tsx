import { Cloud, HardDrive } from 'lucide-react'

export type ModelLocation = 'cloud' | 'local'

export function ModelLocationOptions({
  value,
  onChange,
}: {
  value: ModelLocation
  onChange: (location: ModelLocation) => void
}) {
  const options: Array<{
    value: ModelLocation
    label: string
    description: string
    icon: typeof Cloud
  }> = [
    {
      value: 'cloud',
      label: 'Cloud',
      description: 'Connect to a remotely hosted service.',
      icon: Cloud,
    },
    {
      value: 'local',
      label: 'Local',
      description: 'Connect to a service running in your environment.',
      icon: HardDrive,
    },
  ]

  return (
    <fieldset className="model-location-options field--full">
      <legend>Location</legend>
      <div className="model-location-options__grid">
        {options.map((option) => {
          const Icon = option.icon
          return (
            <label
              key={option.value}
              className={`model-location-option ${value === option.value ? 'is-selected' : ''}`}
            >
              <input
                type="radio"
                name="location"
                value={option.value}
                checked={value === option.value}
                onChange={() => onChange(option.value)}
              />
              <span className="model-location-option__icon" aria-hidden="true">
                <Icon size={16} />
              </span>
              <span className="model-location-option__copy">
                <strong>{option.label}</strong>
                <small>{option.description}</small>
              </span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}
