import { Bot, Mic, Send, Settings2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Notification } from '../../api/types'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { useProfile } from '../../hooks/useStudioData'
import { FluidVoiceOrb, type VoiceState } from './FluidVoiceOrb'
import { useChatSocket } from './useChatSocket'

function notificationText(item: Notification) {
  return item.message || item.content || ''
}
function formatTime(value?: string) {
  return value ? new Date(value).toLocaleString() : 'Just now'
}

export function ChatPage() {
  const profile = useProfile().data
  const assistantName = profile?.assistant_name || 'Mounir'
  const [notifications, setNotifications] = useState<Notification[]>([])
  const addHeartbeat = useCallback(
    (text: string) =>
      setNotifications((current) => [
        { message: text, created_at: new Date().toISOString() },
        ...current,
      ]),
    [],
  )
  const chat = useChatSocket(addHeartbeat)
  const [text, setText] = useState('')
  const [voiceState, setVoiceState] = useState<VoiceState>('ready')
  const [voiceStream, setVoiceStream] = useState<MediaStream | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const bottom = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    document.title = assistantName
  }, [assistantName])
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chat.messages, chat.streaming])
  useEffect(() => {
    fetch('/api/heartbeat/notifications')
      .then((r) => r.json())
      .then((data) => setNotifications(data.notifications || []))
      .catch(() => undefined)
  }, [])

  const submit = () => {
    if (chat.send(text)) setText('')
  }
  const toggleVoice = async () => {
    if (voiceState === 'listening') {
      recorder.current?.stop()
      return
    }
    if (voiceState !== 'ready') return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      setVoiceStream(stream)
      chunks.current = []
      const mediaRecorder = new MediaRecorder(stream)
      recorder.current = mediaRecorder
      mediaRecorder.ondataavailable = (event) => event.data.size && chunks.current.push(event.data)
      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop())
        setVoiceStream(null)
        setVoiceState('thinking')
        const body = new FormData()
        body.append(
          'file',
          new Blob(chunks.current, { type: mediaRecorder.mimeType }),
          'voice.webm',
        )
        try {
          const response = await fetch('/api/voice', { method: 'POST', body })
          const data = await response.json()
          if (!response.ok) throw new Error(data.error || 'Voice request failed.')
          if (data.text) chat.appendVoiceTurn(data.text, data.reply || '')
          if (data.audio_b64) {
            setVoiceState('speaking')
            const audio = new Audio(`data:audio/wav;base64,${data.audio_b64}`)
            audio.onended = () => setVoiceState('ready')
            audio.onerror = () => setVoiceState('ready')
            await audio.play()
          } else setVoiceState('ready')
        } catch (error) {
          chat.appendVoiceTurn(
            'Voice input',
            error instanceof Error ? error.message : 'Voice request failed.',
          )
          setVoiceState('ready')
        }
      }
      mediaRecorder.start()
      setVoiceState('listening')
    } catch {
      setVoiceStream(null)
      setVoiceState('ready')
    }
  }

  return (
    <div className="chat-shell">
      <aside className="chat-sidebar">
        <div className="chat-brand">
          <div className="brand">
            <span className="brand__mark">
              <Bot size={21} />
            </span>
            <span>
              <strong>MOUNIR</strong>
              <small>Local assistant</small>
            </span>
          </div>
        </div>
        <div className="orb-wrap">
          <div>
            <FluidVoiceOrb state={voiceState} stream={voiceStream} onClick={toggleVoice} />
            <div className="voice-state">
              {voiceState}
              <p>
                {voiceState === 'listening' ? 'Tap again when finished' : 'Tap the orb to speak'}
              </p>
            </div>
          </div>
        </div>
        <a href="/admin" className="studio-link">
          <Settings2 size={15} /> Open Agent Studio
        </a>
      </aside>
      <main className="chat-main">
        <header className="chat-header">
          <div>
            <h1>{assistantName}</h1>
            <p>Private conversation on this device</p>
          </div>
          <span className={`connection-pill ${chat.connection === 'online' ? 'online' : ''}`}>
            <i />
            {chat.connection}
          </span>
        </header>
        <div className="transcript" aria-live="polite">
          {!chat.messages.length && (
            <div className="empty-state">Ask {assistantName} anything, or use the voice orb.</div>
          )}
          {chat.messages.map((message, index) => (
            <div
              key={index}
              className={`message message--${message.role} ${chat.streaming && message.role === 'assistant' && index === chat.messages.length - 1 ? 'message--streaming' : ''}`}
            >
              <div className="message__bubble">{message.content}</div>
            </div>
          ))}
          <div ref={bottom} />
        </div>
        <div className="composer-wrap">
          <div className="composer">
            <textarea
              value={text}
              rows={1}
              placeholder={`Message ${assistantName}`}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  submit()
                }
              }}
              disabled={chat.streaming}
            />
            <button
              className="icon-button composer-voice"
              onClick={toggleVoice}
              disabled={voiceState === 'thinking' || voiceState === 'speaking'}
              aria-label={voiceState === 'listening' ? 'Stop recording' : 'Start voice input'}
            >
              <Mic size={17} />
            </button>
            <button
              className="icon-button"
              onClick={submit}
              disabled={!text.trim() || chat.streaming || chat.connection !== 'online'}
              aria-label="Send"
            >
              <Send size={17} />
            </button>
          </div>
        </div>
      </main>
      <aside className="notification-panel">
        <h2>
          Heartbeat <span>{notifications.length}</span>
        </h2>
        <div className="notification-list">
          {!notifications.length && <div className="empty-state">No updates yet.</div>}
          {notifications.map((item, index) => (
            <article className="notification" key={item.id || index}>
              <p>{notificationText(item)}</p>
              <time>{formatTime(item.created_at)}</time>
            </article>
          ))}
        </div>
      </aside>
      <ConfirmDialog
        open={Boolean(chat.confirmation)}
        title="Allow this action?"
        message={chat.confirmation?.prompt || ''}
        confirmLabel="Allow"
        onConfirm={() => chat.answerConfirmation(true)}
        onCancel={() => chat.answerConfirmation(false)}
      />
    </div>
  )
}
