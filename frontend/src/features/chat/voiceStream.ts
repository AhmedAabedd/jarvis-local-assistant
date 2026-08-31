export type VoiceStreamEvent =
  | { type: 'transcript'; text: string; lang?: string }
  | {
      type: 'completion'
      index: number
      text: string
      has_tool_calls: boolean
      is_final: boolean
    }
  | { type: 'audio'; index: number; audio_b64: string; audio_mime?: string }
  | { type: 'voice_error'; index: number; text: string; message: string }
  | { type: 'error'; message: string }
  | { type: 'done'; reply: string }

export async function* readVoiceStream(response: Response): AsyncGenerator<VoiceStreamEvent> {
  if (!response.body) throw new Error('The voice response could not be streamed.')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let pending = ''

  const parse = (line: string) => JSON.parse(line) as VoiceStreamEvent

  while (true) {
    const { done, value } = await reader.read()
    pending += decoder.decode(value, { stream: !done })
    const lines = pending.split('\n')
    pending = lines.pop() || ''
    for (const line of lines) {
      if (line.trim()) yield parse(line)
    }
    if (done) break
  }

  if (pending.trim()) yield parse(pending)
}

export function playVoiceAudio(audioBase64: string, mimeType = 'audio/wav') {
  return new Promise<void>((resolve) => {
    const audio = new Audio(`data:${mimeType};base64,${audioBase64}`)
    let finished = false
    const finish = () => {
      if (finished) return
      finished = true
      resolve()
    }
    audio.onended = finish
    audio.onerror = finish
    void audio.play().catch(finish)
  })
}
