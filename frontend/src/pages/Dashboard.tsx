import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { NotificationSub as VoiceConfig, CourseItem } from '../types'
import { useAccounts } from '../hooks/useAccounts'

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
  problemtype?: number
  source?: string
}

const VOICE_SUBOPTION: Partial<Record<string, keyof Omit<VoiceConfig, 'enabled'>>> = {
  signin: 'signin',
  problem: 'problem',
  problem_received: 'problem',
  call: 'call',
  danmu: 'danmu',
  red_packet: 'red_packet',
}

let eventCounter = 0

function formatEventLabel(event: ActivityEvent, t: (key: string) => string): string {
  const typeName = t(`events.${event.type}`) || event.type
  const lesson = event.lesson ? `[${event.lesson}] ` : ''

  switch (event.type) {
    case 'signin':
      return `${lesson}${typeName}: ${t(`events.${event.status || 'success'}`)}`
    case 'problem_received':
      return `${lesson}${typeName}`
    case 'problem': {
      const problemTypeName = event.problemtype
        ? t(`events.problemType${event.problemtype}`)
        : typeName
      if (event.status === 'ai_failed') {
        return `${lesson}${problemTypeName}: ${t('events.ai_failed')}`
      }
      const statusText = t(`events.${event.status || 'success'}`)
      const answerText = event.answers
        ? Array.isArray(event.answers)
          ? event.answers.join(', ')
          : typeof event.answers === 'object'
            ? JSON.stringify(event.answers)
            : String(event.answers)
        : ''
      const sourceText = event.source ? ` [${t(`events.source_${event.source}`)}]` : ''
      return `${lesson}${problemTypeName}: ${statusText}${answerText ? `, ${t('events.answer')}: ${answerText}` : ''}${sourceText}`
    }
    case 'danmu':
      return `${lesson}${typeName}: "${event.content || ''}" — ${t(`events.${event.status || 'success'}`)}`
    case 'call':
      return `${lesson}${typeName}`
    case 'red_packet':
      return `${lesson}${typeName}: ${t(`events.${event.status || 'success'}`)}`
    case 'session_expired':
      return `${typeName}`
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
    case 'problem_received':
      return isChinese ? `${lesson}收到题目` : `${lesson} problem received`
    case 'problem':
      if (event.status === 'ai_failed') {
        return isChinese ? `${lesson}AI答题失败，请手动作答` : `${lesson} AI failed, please answer manually`
      }
      return isChinese ? `${lesson}已答题` : `${lesson} answered`
    case 'call':
      return isChinese ? '您被点名' : 'You were called on'
    case 'danmu':
      return isChinese ? '弹幕已发送' : 'Danmu sent'
    case 'red_packet':
      return isChinese ? `${lesson}已抢红包` : `${lesson} red packet grabbed`
    default:
      return ''
  }
}

function eventBadgeClass(event: ActivityEvent): string {
  if (event.type === 'session_expired') return 'badge badge-red'
  if (event.type === 'lesson_end') return 'badge badge-gray'
  if (event.type === 'lesson_start') return 'badge badge-green'
  if (event.type === 'red_packet') return event.status === 'success' ? 'badge badge-green' : 'badge badge-red'
  if (event.type === 'problem_received') return 'badge badge-blue'
  if (event.type === 'call') return 'badge badge-yellow'
  if (event.type === 'network')
    return event.status === 'error' ? 'badge badge-red' : 'badge badge-green'
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

  const voiceConfigsRef = useRef<Record<string, VoiceConfig>>({})
  const notifConfigsRef = useRef<Record<string, VoiceConfig>>({})
  const lessonToClassroomRef = useRef<Record<string, string>>({})
  const langRef = useRef(i18n.language)

  useEffect(() => {
    langRef.current = i18n.language
  }, [i18n.language])

  const fetchAllCourses = useCallback(() => {
    if (!accountId) return
    fetch(`/api/accounts/${accountId}/courses/all`)
      .then((r) => r.json())
      .then((data: CourseItem[]) => setAllCourses(data))
      .catch(() => {})
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
      .catch(() => {})
  }, [accountId])

  const fetchCourseConfigs = useCallback(() => {
    if (!accountId) return
    fetch(`/api/accounts/${accountId}/courses/settings`)
      .then((r) => r.json())
      .then((data: Record<string, { notification?: VoiceConfig; voice_notification?: VoiceConfig }>) => {
        const voiceMap: Record<string, VoiceConfig> = {}
        const notifMap: Record<string, VoiceConfig> = {}
        const defaults: VoiceConfig = { enabled: true, signin: true, problem: true, call: true, danmu: false, red_packet: true }
        for (const [id, cfg] of Object.entries(data)) {
          notifMap[id] = cfg.notification ?? { ...defaults }
          voiceMap[id] = cfg.voice_notification ?? { ...defaults, enabled: false }
        }
        notifConfigsRef.current = notifMap
        voiceConfigsRef.current = voiceMap
      })
      .catch(() => {})
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
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utter)
  }

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
        const t = msg['type'] as string
        if (t === 'heartbeat') return

        if (t === 'history') {
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
            problemtype: m['problemtype'] as number | undefined,
            source: m['source'] as string | undefined,
          }))
          setEvents(historical.reverse())
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
          problemtype: msg['problemtype'] as number | undefined,
          source: msg['source'] as string | undefined,
        }

        setEvents((prev) => [event, ...prev].slice(0, 50))

        if (event.type === 'lesson_start' || event.type === 'lesson_end') {
          fetchAllCourses()
          fetchLessons()
          fetchCourseConfigs()
        }

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
  }, [accountId, fetchAllCourses, fetchLessons, fetchCourseConfigs])

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = 0
    }
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
                    : t(`events.${event.type}`) || event.type}
                </span>
                <span className="activity-text">{formatEventLabel(event, t)}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
