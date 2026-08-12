import { Bell, Bot, Check, History, Inbox, Mic, Send, Settings2, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
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
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [showNotificationHistory, setShowNotificationHistory] = useState(false)
  const [notificationToast, setNotificationToast] = useState<{
    id: number
    title: string
    text: string
  } | null>(null)
  const notificationToastTimer = useRef<number | null>(null)
  const notificationToastId = useRef(0)
  const loadNotifications = useCallback(async () => {
    try {
      const data = await api.heartbeat.notifications()
      setNotifications(data.notifications || [])
    } catch {
      // A notification refresh should never interrupt chat.
    }
  }, [])
  const addHeartbeat = useCallback(
    (text: string, taskName?: string) => {
      void loadNotifications()
      const message = text.replace(/^Heartbeat update[^\n]*\s*/i, '').trim()
      if (!message) return

      notificationToastId.current += 1
      setNotificationToast({
        id: notificationToastId.current,
        title: taskName || 'New notification',
        text: message,
      })
      if (notificationToastTimer.current) window.clearTimeout(notificationToastTimer.current)
      notificationToastTimer.current = window.setTimeout(() => {
        setNotificationToast(null)
        notificationToastTimer.current = null
      }, 5000)
    },
    [loadNotifications],
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
    void loadNotifications()
  }, [loadNotifications])
  useEffect(
    () => () => {
      if (notificationToastTimer.current) window.clearTimeout(notificationToastTimer.current)
    },
    [],
  )
  useEffect(() => {
    if (!notificationsOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setNotificationsOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [notificationsOpen])

  const markNotificationRead = async (item: Notification) => {
    if (!item.id) return
    await api.heartbeat.markNotificationRead(item.id)
    setNotifications((current) =>
      current.map((candidate) =>
        candidate.id === item.id ? { ...candidate, read_at: new Date().toISOString() } : candidate,
      ),
    )
  }
  const deleteNotification = async (item: Notification) => {
    if (!item.id) return
    await api.heartbeat.deleteNotification(item.id)
    setNotifications((current) => current.filter((candidate) => candidate.id !== item.id))
  }
  const unreadNotifications = notifications.filter((item) => !item.read_at)
  const readNotifications = notifications.filter((item) => item.read_at)
  const visibleNotifications = showNotificationHistory ? readNotifications : unreadNotifications

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
          <div className="chat-header__actions">
            <span className={`connection-pill ${chat.connection === 'online' ? 'online' : ''}`}>
              <i />
              {chat.connection}
            </span>
            <button
              className="notification-trigger"
              type="button"
              aria-label={`Open notifications, ${unreadNotifications.length} unread`}
              aria-expanded={notificationsOpen}
              onClick={() => setNotificationsOpen(true)}
            >
              <Bell size={18} />
              {unreadNotifications.length > 0 && (
                <span className="notification-trigger__badge" aria-hidden="true">
                  {unreadNotifications.length > 99 ? '99+' : unreadNotifications.length}
                </span>
              )}
            </button>
          </div>
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
      {notificationToast && (
        <div
          className="notification-toast"
          key={notificationToast.id}
          role="status"
          aria-live="polite"
        >
          <span className="notification-toast__icon" aria-hidden="true">
            <Bell size={16} />
          </span>
          <button
            className="notification-toast__content"
            type="button"
            onClick={() => {
              setNotificationToast(null)
              setNotificationsOpen(true)
            }}
          >
            <strong>{notificationToast.title}</strong>
            <span>{notificationToast.text}</span>
          </button>
          <button
            className="notification-toast__close"
            type="button"
            aria-label="Dismiss notification"
            onClick={() => setNotificationToast(null)}
          >
            <X size={14} />
          </button>
        </div>
      )}
      {notificationsOpen && (
        <button
          className="notification-backdrop"
          type="button"
          aria-label="Close notifications"
          onClick={() => setNotificationsOpen(false)}
        />
      )}
      <aside
        className={`notification-panel ${notificationsOpen ? 'notification-panel--open' : ''}`}
        aria-hidden={!notificationsOpen}
        aria-label="Notifications"
      >
        <div className="notification-panel__header">
          <div>
            <h2>Notifications</h2>
            <p>{unreadNotifications.length} unread</p>
          </div>
          <button
            className="notification-panel__close"
            type="button"
            aria-label="Close notifications"
            onClick={() => setNotificationsOpen(false)}
          >
            <X size={17} />
          </button>
        </div>
        <div className="notification-panel__toolbar">
          <button
            className="notification-view-button"
            type="button"
            onClick={() => setShowNotificationHistory((current) => !current)}
          >
            {showNotificationHistory ? <Inbox size={13} /> : <History size={13} />}
            {showNotificationHistory ? 'Unread' : 'History'}
          </button>
        </div>
        <div className="notification-list">
          {!visibleNotifications.length && (
            <div className="empty-state">
              {showNotificationHistory ? 'No read notifications.' : 'You are all caught up.'}
            </div>
          )}
          {visibleNotifications.map((item, index) => (
            <article className="notification" key={item.id || index}>
              <div className="notification__actions">
                {!showNotificationHistory && (
                  <button
                    type="button"
                    title="Mark as read"
                    aria-label="Mark notification as read"
                    onClick={() => void markNotificationRead(item)}
                  >
                    <Check size={12} />
                  </button>
                )}
                <button
                  type="button"
                  title="Delete permanently"
                  aria-label="Delete notification permanently"
                  onClick={() => void deleteNotification(item)}
                >
                  <X size={12} />
                </button>
              </div>
              {item.heartbeat_task_name && (
                <strong className="notification__task">{item.heartbeat_task_name}</strong>
              )}
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
