export function objectValue(value: unknown): Record<string, string> {
  if (!value) return {}
  if (typeof value === 'object' && !Array.isArray(value)) return value as Record<string, string>
  try {
    const parsed = JSON.parse(String(value))
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

export function stringList(value: unknown, fallback: string[] = []): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean)
  if (!value) return fallback
  try {
    const parsed = JSON.parse(String(value))
    return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : fallback
  } catch {
    return fallback
  }
}

export function readable(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

export function remoteAuth(headers: Record<string, string>, scheme = '') {
  const entries = Object.entries(headers)
  if (scheme === 'oauth')
    return { mode: 'oauth', method: 'bearer', secret: '', header: 'X-API-Key' }
  if (scheme === 'bearer') {
    const match = entries[0]?.[1].match(/^Bearer\s+(.+)$/i)
    return {
      mode: 'credential',
      method: 'bearer',
      secret: match?.[1] || '',
      header: 'X-API-Key',
    }
  }
  if (scheme === 'header')
    return {
      mode: 'credential',
      method: 'header',
      secret: entries[0]?.[1] || '',
      header: entries[0]?.[0] || 'X-API-Key',
    }
  if (!entries.length) return { mode: 'none', method: 'bearer', secret: '', header: 'X-API-Key' }
  if (scheme === 'custom')
    return { mode: 'custom', method: 'bearer', secret: '', header: 'X-API-Key' }
  if (entries.length === 1 && entries[0][0].toLowerCase() === 'authorization') {
    const match = entries[0][1].match(/^Bearer\s+(.+)$/i)
    return {
      mode: 'credential',
      method: 'bearer',
      secret: match?.[1] || '',
      header: 'X-API-Key',
    }
  }
  if (entries.length === 1)
    return { mode: 'credential', method: 'header', secret: entries[0][1], header: entries[0][0] }
  return { mode: 'custom', method: 'bearer', secret: '', header: 'X-API-Key' }
}

export function toDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}
