import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import type { CourseItem, NotificationSub } from '../types'
import { useAccounts } from '../hooks/useAccounts'
import MiddleTruncate from '../components/MiddleTruncate'

interface ActiveLesson {
  lessonid: number
  lessonname: string
  classroomid: number
}

interface ActivityEvent {
  id: number
  timestamp: string
  type: string
  lesson?: string
  lessonid?: number
  status?: string
  message?: string
  content?: string
  // Type 1/2/3 send list[str]; type 5 (short answer) sends a string.
  answers?: unknown
  problemid?: unknown
  problemtype?: number
  source?: string
}

// Event types that have a corresponding course notification sub-toggle.
// Mirrors backend/pushdeer.py:_EVENT_SUBKEY.
const NOTIF_SUBKEY: Partial<Record<string, keyof Omit<NotificationSub, 'enabled'>>> = {
  signin: 'signin',
  problem: 'problem',
  problem_received: 'problem',
  call: 'call',
  danmu: 'danmu',
  red_packet: 'red_packet',
}

function localTimeString(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function asEvent(m: Record<string, unknown>, id: number, timestamp: string): ActivityEvent {
  return {
    id,
    timestamp,
    type: String(m.type),
    lesson: m.lesson as string | undefined,
    lessonid: m.lessonid as number | undefined,
    status: m.status as string | undefined,
    message: m.message as string | undefined,
    content: m.content as string | undefined,
    answers: m.answers,
    problemid: m.problemid,
    problemtype: m.problemtype as number | undefined,
    source: m.source as string | undefined,
  }
}

function answersText(answers: unknown): string {
  if (answers == null) return ''
  if (Array.isArray(answers)) return answers.join(', ')
  if (typeof answers === 'object') return JSON.stringify(answers)
  return String(answers)
}

// Row text is the lesson plus details; the badge already names the event type.
// Keep in sync with backend/pushdeer.py:_format_label.
type Fmt = (event: ActivityEvent, t: TFunction) => string

const statusText: Fmt = (e, t) => t(`events.${e.status || 'success'}`)
const nothing: Fmt = () => ''

const DETAILS: Record<string, Fmt> = {
  signin: statusText,
  red_packet: statusText,
  problem: (e, t) => {
    if (e.status === 'ai_failed') return t('events.ai_failed')
    if (e.status === 'skipped') return t(`events.skip_${e.message}`)
    const text = answersText(e.answers)
    const answerSuffix = text ? `, ${t('events.answer')}: ${text}` : ''
    const sourceText = e.source ? ` [${t(`events.source_${e.source}`)}]` : ''
    return `${statusText(e, t)}${answerSuffix}${sourceText}`
  },
  danmu: (e, t) => `"${e.content || ''}" — ${statusText(e, t)}`,
  session_expired: (_e, t) => t('events.session_expired_hint'),
  problem_received: nothing,
  call: nothing,
  lesson_start: nothing,
  lesson_end: nothing,
}

function formatEventLabel(event: ActivityEvent, t: TFunction): string {
  const detail = (DETAILS[event.type] ?? ((e: ActivityEvent) => e.message || ''))(event, t)
  if (!event.lesson) return detail
  return detail ? `${event.lesson}: ${detail}` : event.lesson
}

// Speech text via i18n — see locales/*.json "speech".
function buildSpeechText(event: ActivityEvent, t: TFunction): string {
  const lesson = event.lesson || ''
  switch (event.type) {
    case 'signin':           return t('speech.signin', { lesson })
    case 'problem_received': return t('speech.problem_received', { lesson })
    case 'problem':
      if (event.status === 'ai_failed') return t('speech.problem_ai_failed', { lesson })
      if (event.status === 'skipped') return event.message === 'answered' ? '' : t('speech.problem_skipped', { lesson })
      return t('speech.problem_answered', { lesson })
    case 'call':             return t('speech.call')
    case 'danmu':            return t('speech.danmu')
    case 'red_packet':       return t('speech.red_packet', { lesson })
    default:                 return ''
  }
}

// Failed events carry their cause in `message`; shown under the row and
// appended to PushDeer pushes (backend/pushdeer.py:format_event).
function failureReason(event: ActivityEvent): string {
  return event.status === 'error' || event.status === 'ai_failed' ? event.message || '' : ''
}

function eventBadgeClass(event: ActivityEvent): string {
  if (event.type === 'session_expired') return 'badge badge-red'
  if (event.type === 'lesson_end' || event.status === 'skipped') return 'badge badge-gray'
  if (event.type === 'lesson_start') return 'badge badge-green'
  if (event.type === 'red_packet') return event.status === 'success' ? 'badge badge-green' : 'badge badge-red'
  if (event.type === 'problem_received') return 'badge badge-blue'
  if (event.type === 'call') return 'badge badge-yellow'
  if (event.status === 'success') return 'badge badge-green'
  if (event.status === 'error' || event.status === 'ai_failed') return 'badge badge-red'
  return 'badge badge-blue'
}

export default function Dashboard() {
  const { t, i18n } = useTranslation()
  const { activeAccount } = useAccounts()
  const accountId = activeAccount?.id ?? null
  const [allCourses, setAllCourses] = useState<CourseItem[]>([])
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const logRef = useRef<HTMLDivElement>(null)

  const eventCounter = useRef(0)
  const voiceConfigsRef = useRef<Record<string, NotificationSub>>({})
  const notifConfigsRef = useRef<Record<string, NotificationSub>>({})
  const lessonToClassroomRef = useRef<Record<string, string>>({})

  // Stable refs to t / language so the WebSocket handler doesn't have to
  // re-subscribe every time i18n updates.
  const tRef = useRef(t)
  const langRef = useRef(i18n.language)
  useEffect(() => {
    tRef.current = t
    langRef.current = i18n.language
  }, [t, i18n.language])

  const fetchAllCourses = useCallback(() => {
    if (!accountId) return
    fetch(`/api/accounts/${accountId}/courses/all`)
      .then((r) => r.json())
      .then((data: CourseItem[]) => setAllCourses(data))
      .catch((e) => console.warn('fetchAllCourses failed', e))
  }, [accountId])

  const fetchLessons = useCallback(() => {
    if (!accountId) return
    fetch(`/api/accounts/${accountId}/courses/active`)
      .then((r) => r.json())
      .then((data: { lessons: ActiveLesson[] }) => {
        const map: Record<string, string> = {}
        for (const l of data.lessons) {
          map[String(l.lessonid)] = String(l.classroomid)
        }
        lessonToClassroomRef.current = map
      })
      .catch((e) => console.warn('fetchLessons failed', e))
  }, [accountId])

  const fetchCourseConfigs = useCallback(() => {
    if (!accountId) return
    fetch(`/api/accounts/${accountId}/courses/settings`)
      .then((r) => r.json())
      .then((data: Record<string, { notification: NotificationSub; voice_notification: NotificationSub }>) => {
        const voiceMap: Record<string, NotificationSub> = {}
        const notifMap: Record<string, NotificationSub> = {}
        for (const [id, cfg] of Object.entries(data)) {
          notifMap[id] = cfg.notification
          voiceMap[id] = cfg.voice_notification
        }
        notifConfigsRef.current = notifMap
        voiceConfigsRef.current = voiceMap
      })
      .catch((e) => console.warn('fetchCourseConfigs failed', e))
  }, [accountId])

  // Reload whenever active account changes
  useEffect(() => {
    if (!accountId) {
      setAllCourses([])
      setEvents([])
      return
    }
    setEvents([]) // clear stale events from previous account
    fetchAllCourses()
    fetchLessons()
    fetchCourseConfigs()
  }, [accountId, fetchAllCourses, fetchLessons, fetchCourseConfigs])

  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission()
    }
  }, [])

  const notify = useCallback((event: ActivityEvent) => {
    if (!('Notification' in window) || Notification.permission !== 'granted') return
    const body = buildSpeechText(event, tRef.current)
    if (!body) return
    const title = event.lesson ?? tRef.current('nav.brand')
    new Notification(title, { body, silent: true })
  }, [])

  const speak = useCallback((text: string) => {
    if (!text || !window.speechSynthesis) return
    const utter = new SpeechSynthesisUtterance(text)
    utter.lang = langRef.current.startsWith('zh') ? 'zh-CN' : 'en-US'
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utter)
  }, [])

  // Per-account WebSocket subscription
  useEffect(() => {
    if (!accountId) return
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let unmounted = false

    function connect() {
      if (unmounted || !accountId) return
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${protocol}://${window.location.host}/ws/accounts/${accountId}/events`)

      ws.onmessage = (ev) => {
        let msg: Record<string, unknown>
        try {
          msg = JSON.parse(ev.data as string) as Record<string, unknown>
        } catch {
          return
        }
        const type = msg['type'] as string
        if (type === 'heartbeat') return

        if (type === 'history') {
          const raw = (msg['events'] as Record<string, unknown>[]) ?? []
          const historical = raw.map((m) => {
            const logged = m['logged_at'] as string | undefined
            const ts = logged ? localTimeString(new Date(logged)) : ''
            return asEvent(m, ++eventCounter.current, ts)
          })
          setEvents(historical.reverse())
          fetchAllCourses()
          fetchLessons()
          fetchCourseConfigs()
          return
        }

        const event = asEvent(msg, ++eventCounter.current, localTimeString(new Date()))
        setEvents((prev) => [event, ...prev].slice(0, 50))

        if (event.type === 'lesson_start' || event.type === 'lesson_end') {
          fetchAllCourses()
          fetchLessons()
          fetchCourseConfigs()
        }

        const subKey = NOTIF_SUBKEY[event.type]
        if (subKey) {
          const courseId = lessonToClassroomRef.current[String(event.lessonid)] ?? String(event.lessonid)
          const notifCfg = notifConfigsRef.current[courseId]
          if (notifCfg?.enabled && notifCfg[subKey]) notify(event)
          const voiceCfg = voiceConfigsRef.current[courseId]
          if (voiceCfg?.enabled && voiceCfg[subKey]) speak(buildSpeechText(event, tRef.current))
        }
      }

      ws.onerror = () => {}
      ws.onclose = () => {
        if (!unmounted) {
          reconnectTimer = setTimeout(connect, 3000)
        }
      }
    }

    connect()

    return () => {
      unmounted = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [accountId, fetchAllCourses, fetchLessons, fetchCourseConfigs, notify, speak])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = 0
  }, [events])

  return (
    <div className="page">
      <section className="card">
        <h2 className="card-title">{t('dashboard.allCourses')}</h2>
        {allCourses.length === 0 ? (
          <p className="empty-message">{t('dashboard.noCourses')}</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('dashboard.course')}</th>
                <th>{t('dashboard.teacher')}</th>
                <th>{t('dashboard.status')}</th>
              </tr>
            </thead>
            <tbody>
              {allCourses.map((course) => (
                <tr key={course.classroom_id}>
                  <td className="cell-fill"><MiddleTruncate text={course.name} /></td>
                  <td>{course.teacher_name ?? t('common.unknown')}</td>
                  <td>
                    <span className={`badge ${course.active ? 'badge-green' : 'badge-gray'}`}>
                      {course.active ? t('dashboard.active') : t('dashboard.inactive')}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card">
        <h2 className="card-title">{t('dashboard.recentActivity')}</h2>
        {events.length === 0 ? (
          <p className="empty-message">{t('dashboard.noActivity')}</p>
        ) : (
          <div className="activity-log" ref={logRef}>
            {events.map((event) => (
              <div key={event.id} className="activity-entry">
                <span className="activity-time">{event.timestamp}</span>
                <span className={eventBadgeClass(event)}>
                  {event.type === 'problem' && event.problemtype
                    ? t(`events.problemType${event.problemtype}`)
                    : t(`events.${event.type}`)}
                </span>
                <span className="activity-text">
                  {formatEventLabel(event, t)}
                  {failureReason(event) && (
                    <span className="activity-detail">{t('events.reason')}: {failureReason(event)}</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
