import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChatMessage } from '../../api/types'

type Connection = 'connecting' | 'online' | 'offline'
interface Confirmation {
  id: string
  prompt: string
}

export function useChatSocket(onHeartbeat: (text: string, title?: string) => void) {
  const socket = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<number | null>(null)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const onHeartbeatRef = useRef(onHeartbeat)
  onHeartbeatRef.current = onHeartbeat

  useEffect(() => {
    fetch('/api/conversation')
      .then((r) => r.json())
      .then((data) => setMessages(data.messages || []))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    let disposed = false
    const connect = () => {
      if (disposed) return
      setConnection('connecting')
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${protocol}//${location.host}/ws/chat`)
      socket.current = ws
      ws.onopen = () => setConnection('online')
      ws.onclose = () => {
        if (disposed) return
        setConnection('offline')
        setStreaming(false)
        streamingRef.current = false
        reconnectTimer.current = window.setTimeout(connect, 1800)
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (event) => {
        const packet = JSON.parse(event.data)
        if (packet.type === 'chunk') {
          setMessages((current) => {
            const copy = [...current]
            const last = copy.at(-1)
            if (last?.role === 'assistant' && streamingRef.current) {
              copy[copy.length - 1] = {
                ...last,
                content: last.content + (packet.text || ''),
              }
            } else copy.push({ role: 'assistant', content: packet.text || '' })
            return copy
          })
          streamingRef.current = true
        } else if (packet.type === 'done') {
          streamingRef.current = false
          setStreaming(false)
        } else if (packet.type === 'error') {
          setMessages((current) => [
            ...current,
            { role: 'assistant', content: `Something went wrong: ${packet.message}` },
          ])
          streamingRef.current = false
          setStreaming(false)
        } else if (packet.type === 'confirm') {
          setConfirmation({ id: packet.id, prompt: packet.prompt })
        } else if (packet.type === 'heartbeat')
          onHeartbeatRef.current(packet.text, packet.title || undefined)
      }
    }
    connect()
    return () => {
      disposed = true
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      socket.current?.close()
    }
  }, [])

  const streamingRef = useRef(false)
  const send = useCallback(
    (text: string) => {
      const value = text.trim()
      if (!value || connection !== 'online' || streamingRef.current) return false
      setMessages((current) => [...current, { role: 'user', content: value }])
      setStreaming(true)
      streamingRef.current = true
      socket.current?.send(JSON.stringify({ type: 'user', text: value }))
      return true
    },
    [connection],
  )

  const answerConfirmation = useCallback(
    (approved: boolean) => {
      if (!confirmation) return
      socket.current?.send(
        JSON.stringify({ type: 'confirm_response', id: confirmation.id, approved }),
      )
      setConfirmation(null)
    },
    [confirmation],
  )

  const appendVoiceTurn = useCallback((text: string, reply: string) => {
    setMessages((current) => [
      ...current,
      { role: 'user', content: text },
      { role: 'assistant', content: reply },
    ])
  }, [])

  return {
    connection,
    messages,
    streaming,
    confirmation,
    send,
    answerConfirmation,
    appendVoiceTurn,
  }
}
