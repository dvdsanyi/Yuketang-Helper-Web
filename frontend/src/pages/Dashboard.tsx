import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { NotificationSub as VoiceConfig, CourseItem } from '../types'

interface ActiveLesson {
  lessonid: number
  lessonname: string
  classroomid: number
  teacher_name: string | null
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
  answers?: unknown[]
  problemid?: unknown
}

// Map event type to the VoiceConfig suboption key
const VOICE_SUBOPTION: Partial<Record<string, keyof Omit<VoiceConfig, 'enabled'>>> = {
  signin: 'signin',
  problem: 'problem',
  call: 'call',
  danmu: 'danmu',
}

let eventCounter = 0

function formatEventLabel(event: ActivityEvent, t: (key: string) => string): string {
  const typeName = t(`events.${event.type}`) || event.type
  const lesson = event.lesson ? `[${event.lesson}] ` : ''

  switch (event.type) {
    case 'signin':
      return `${lesson}${typeName}: ${t(`events.${event.status || 'success'}`)}`
    case 'problem':
      return `${lesson}${typeName}: ${t(`events.${event.status || 'success'}`)}`
    case 'danmu':
      return `${lesson}${typeName}: "${event.content || ''}" — ${t(`events.${event.status || 'success'}`)}`
    case 'call':
      return `${lesson}${typeName}`
    case 'lesson_end':
      return `${lesson}${typeName}`
    case 'lesson_start':
      return `${lesson}${typeName}`
    case 'network':
      return `${typeName}: ${event.message || ''}`
    default:
      return `${lesson}${typeName}${event.message ? ': ' + event.message : ''}`
  }
}

function buildSpeechText(event: ActivityEvent, isChinese: boolean): string {
  const lesson = event.lesson || ''
  switch (event.type) {
    case 'signin':
      return isChinese ? `${lesson}已签到` : `${lesson} checked in`
    case 'problem':
      return isChinese ? `${lesson}已答题` : `${lesson} answered`
    case 'call':
      return isChinese ? '您被点名' : 'You were called on'
    case 'danmu':
      return isChinese ? '弹幕已发送' : 'Danmu sent'
    default:
      return ''
  }
}

function eventBadgeClass(event: ActivityEvent): string {
  if (event.type === 'lesson_end') return 'badge badge-gray'
  if (event.type === 'lesson_start') return 'badge badge-green'
  if (event.type === 'call') return 'badge badge-yellow'
  if (event.type === 'network')
    return event.status === 'error' ? 'badge badge-red' : 'badge badge-green'
  if (event.status === 'success') return 'badge badge-green'
  if (event.status === 'error') return 'badge badge-red'
  return 'badge badge-blue'
}

export default function Dashboard() {
  const { t, i18n } = useTranslation()
  const [allCourses, setAllCourses] = useState<CourseItem[]>([])
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const logRef = useRef<HTMLDivElement>(null)

  // Keep per-course configs in refs so the WS closure always reads current values
  const voiceConfigsRef = useRef<Record<string, VoiceConfig>>({})
  const notifConfigsRef = useRef<Record<string, VoiceConfig>>({})
  const lessonToClassroomRef = useRef<Record<string, string>>({})
  const langRef = useRef(i18n.language)

  useEffect(() => {
    langRef.current = i18n.language
  }, [i18n.language])

  const fetchAllCourses = useCallback(() => {
    fetch('/api/courses/all')
      .then((r) => r.json())
      .then((data: CourseItem[]) => setAllCourses(data))
      .catch(() => {})
  }, [])

  const fetchLessons = useCallback(() => {
    fetch('/api/courses/active')
      .then((r) => r.json())
      .then((data: { lessons: ActiveLesson[] }) => {
        const map: Record<string, string> = {}
        for (const l of data.lessons) {
          map[String(l.lessonid)] = String(l.classroomid)
        }
        lessonToClassroomRef.current = map
      })
      .catch(() => {})
  }, [])

  // Fetch and cache per-course notification + voice_notification configs
  const fetchCourseConfigs = useCallback(() => {
    fetch('/api/courses/settings')
      .then((r) => r.json())
      .then((data: Record<string, { notification?: VoiceConfig; voice_notification?: VoiceConfig }>) => {
        const voiceMap: Record<string, VoiceConfig> = {}
        const notifMap: Record<string, VoiceConfig> = {}
        const defaults: VoiceConfig = { enabled: true, signin: true, problem: true, call: true, danmu: false }
        for (const [id, cfg] of Object.entries(data)) {
          notifMap[id] = cfg.notification ?? { ...defaults }
          voiceMap[id] = cfg.voice_notification ?? { ...defaults, enabled: false }
        }
        notifConfigsRef.current = notifMap
        voiceConfigsRef.current = voiceMap
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchAllCourses()
    fetchLessons()
    fetchCourseConfigs()
    const interval = setInterval(fetchLessons, 10_000)
    return () => clearInterval(interval)
  }, [fetchAllCourses, fetchLessons, fetchCourseConfigs])

  // Request OS notification permission once on mount
  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission()
    }
  }, [])

  function notify(event: ActivityEvent) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return
    const isChinese = langRef.current.startsWith('zh')
    const title = event.lesson ?? (isChinese ? '雨课堂助手' : 'Yuketang Helper')
    const body = buildSpeechText(event, isChinese)
    if (!body) return
    new Notification(title, { body, silent: true })
  }

  function speak(text: string) {
    if (!text || !window.speechSynthesis) return
    const utter = new SpeechSynthesisUtterance(text)
    utter.lang = langRef.current.startsWith('zh') ? 'zh-CN' : 'en-US'
    window.speechSynthesis.cancel() // stop any ongoing speech first
    window.speechSynthesis.speak(utter)
  }

  // Connect to events WebSocket with auto-reconnect
  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let unmounted = false

    function connect() {
      if (unmounted) return
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${protocol}://${window.location.host}/ws/events`)

      ws.onmessage = (ev) => {
        let msg: Record<string, unknown>
        try {
          msg = JSON.parse(ev.data as string) as Record<string, unknown>
        } catch {
          return
        }

        if ((msg['type'] as string) === 'heartbeat') return

        // Bulk history sent on first connect
        if ((msg['type'] as string) === 'history') {
          const raw = (msg['events'] as Record<string, unknown>[]) ?? []
          const historical: ActivityEvent[] = raw.map((m) => ({
            id: ++eventCounter,
            timestamp: (m['logged_at'] as string | undefined)?.slice(11, 19) ?? '',
            type: m['type'] as string,
            lesson: m['lesson'] as string | undefined,
            lessonid: m['lessonid'] as number | undefined,
            status: m['status'] as string | undefined,
            message: m['message'] as string | undefined,
            content: m['content'] as string | undefined,
            answers: m['answers'] as unknown[] | undefined,
            problemid: m['problemid'],
          }))
          setEvents(historical.reverse()) // newest first
          fetchAllCourses()
          fetchLessons()
          fetchCourseConfigs()
          return
        }

        const event: ActivityEvent = {
          id: ++eventCounter,
          timestamp: new Date().toTimeString().slice(0, 8),
          type: msg['type'] as string,
          lesson: msg['lesson'] as string | undefined,
          lessonid: msg['lessonid'] as number | undefined,
          status: msg['status'] as string | undefined,
          message: msg['message'] as string | undefined,
          content: msg['content'] as string | undefined,
          answers: msg['answers'] as unknown[] | undefined,
          problemid: msg['problemid'],
        }

        setEvents((prev) => [event, ...prev].slice(0, 50))

        if (event.type === 'lesson_start' || event.type === 'lesson_end') {
          fetchAllCourses()
          fetchLessons()
          fetchCourseConfigs()
        }

        // OS notification + voice notification
        const subKey = VOICE_SUBOPTION[event.type]
        if (subKey) {
          const courseId = lessonToClassroomRef.current[String(event.lessonid)] ?? String(event.lessonid)
          const notifCfg = notifConfigsRef.current[courseId]
          if (notifCfg?.enabled && notifCfg[subKey]) {
            notify(event)
          }
          const voiceCfg = voiceConfigsRef.current[courseId]
          if (voiceCfg?.enabled && voiceCfg[subKey]) {
            speak(buildSpeechText(event, langRef.current.startsWith('zh')))
          }
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
  }, [fetchAllCourses, fetchLessons, fetchCourseConfigs])

  // Auto-scroll to top of log when new events arrive
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = 0
    }
  }, [events])

  return (
    <div className="page">
      <h1 className="page-title">{t('dashboard.title')}</h1>

      {/* All Courses */}
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
                  <td>{course.name}</td>
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

      {/* Activity Log */}
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
                  {t(`events.${event.type}`) || event.type}
                </span>
                <span className="activity-text">
                  {formatEventLabel(event, t)}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
