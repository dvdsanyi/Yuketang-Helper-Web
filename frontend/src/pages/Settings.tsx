import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { NotificationSub, CourseItem, PushdeerSettings, PushdeerKeyEntry } from '../types'
import { useAccounts } from '../hooks/useAccounts'

interface CourseConfig {
  name: string
  type1: string
  type2: string
  type3: string
  type4: string
  type5: string
  course_enabled: boolean
  answer_last5s: boolean
  auto_danmu: boolean
  auto_redpacket: boolean
  danmu_threshold: number
  notification: NotificationSub
  voice_notification: NotificationSub
  pushdeer_notification: NotificationSub
}

interface PollIntervalSettings {
  poll_interval: number
  default: number
  min: number
  max: number
}

interface CourseState extends CourseConfig {
  courseId: string
  saveStatus: 'idle' | 'saving' | 'saved' | 'error'
}

interface AIKeyEntry {
  name: string
  provider: string
  key: string
}

const PROVIDER_LABELS: Record<string, string> = {
  google: 'Google',
  qwen: 'ModelScope',
}

interface AISettings {
  keys: AIKeyEntry[]
  active_key: number
  fallback_keys: boolean
}

type CoursesMap = Record<string, CourseConfig>

function buildCourseStates(allCourses: CourseItem[], settings: CoursesMap, defaults: CourseConfig): CourseState[] {
  return allCourses.map((c) => {
    const cfg = settings[c.classroom_id] ?? {} as Partial<CourseConfig>
    return {
      courseId: c.classroom_id,
      name: c.name,
      type1: cfg.type1 ?? defaults.type1,
      type2: cfg.type2 ?? defaults.type2,
      type3: cfg.type3 ?? defaults.type3,
      type4: cfg.type4 ?? defaults.type4,
      type5: cfg.type5 ?? defaults.type5,
      course_enabled: cfg.course_enabled ?? defaults.course_enabled,
      answer_last5s: cfg.answer_last5s ?? defaults.answer_last5s,
      auto_danmu: cfg.auto_danmu ?? defaults.auto_danmu,
      auto_redpacket: cfg.auto_redpacket ?? defaults.auto_redpacket,
      danmu_threshold: cfg.danmu_threshold ?? defaults.danmu_threshold,
      notification: { ...defaults.notification, ...cfg.notification },
      voice_notification: { ...defaults.voice_notification, ...cfg.voice_notification },
      pushdeer_notification: { ...defaults.pushdeer_notification, ...cfg.pushdeer_notification },
      saveStatus: 'idle',
    }
  })
}

function NotificationSection({
  label,
  value,
  onChange,
  disabled = false,
  disabledTitle,
}: {
  label: string
  value: NotificationSub
  onChange: (v: NotificationSub) => void
  disabled?: boolean
  disabledTitle?: string
}) {
  const { t } = useTranslation()
  const subKeys: (keyof Omit<NotificationSub, 'enabled'>)[] = ['signin', 'problem', 'call', 'danmu', 'red_packet']
  const effectiveEnabled = !disabled && value.enabled

  return (
    <div className="notif-section">
      <div className="form-row">
        <label className="form-label">{label}</label>
        <span
          className="toggle-group-wrap"
          data-tooltip={disabled && disabledTitle ? disabledTitle : undefined}
        >
        <div className="toggle-group">
          <button
            className={`toggle-option ${effectiveEnabled ? 'selected' : ''}`}
            onClick={() => onChange({ ...value, enabled: true })}
            disabled={disabled}
          >
            {t('common.on')}
          </button>
          <button
            className={`toggle-option ${!effectiveEnabled ? 'selected' : ''}`}
            onClick={() => onChange({ ...value, enabled: false })}
            disabled={disabled}
          >
            {t('common.off')}
          </button>
        </div>
        </span>
      </div>
      {effectiveEnabled && (
        <div className="notif-suboptions">
          {subKeys.map((key) => (
            <label key={key} className="notif-sub-item">
              <input
                type="checkbox"
                checked={value[key]}
                onChange={(e) => onChange({ ...value, [key]: e.target.checked })}
              />
              <span>{t(`events.${key}`)}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

function QuizModeSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
}) {
  return (
    <div className="form-row">
      <label className="form-label">{label}</label>
      <select className="form-select" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  )
}

// Full Cartesian product of (mode × answer_last5s × limit) with the resulting
// submission behavior. Mirrors backend/lesson.py:_compute_delay /
// _compute_ai_window. Random and Blank share identical timing logic, so they
// share a single "Fallback" row group.
const TIMING_ROWS: { mode: 'Ai' | 'Fallback'; last5s: 'On' | 'Off'; limit: 'Limited' | 'Unlimited' }[] = [
  { mode: 'Ai',       last5s: 'On',  limit: 'Limited'   },
  { mode: 'Ai',       last5s: 'On',  limit: 'Unlimited' },
  { mode: 'Ai',       last5s: 'Off', limit: 'Limited'   },
  { mode: 'Ai',       last5s: 'Off', limit: 'Unlimited' },
  { mode: 'Fallback', last5s: 'On',  limit: 'Limited'   },
  { mode: 'Fallback', last5s: 'On',  limit: 'Unlimited' },
  { mode: 'Fallback', last5s: 'Off', limit: 'Limited'   },
  { mode: 'Fallback', last5s: 'Off', limit: 'Unlimited' },
]

function TimingTooltip() {
  const { t } = useTranslation()
  const k = (suffix: string) => `settings.answerLast5sTable.${suffix}`
  return (
    <span className="tooltip-trigger">
      ?
      <div className="tooltip-table-popup" role="tooltip">
        <div className="tooltip-table-title">{t(k('title'))}</div>
        <table>
          <thead>
            <tr>
              <th>{t(k('headerMode'))}</th>
              <th>{t(k('headerLast5s'))}</th>
              <th>{t(k('headerLimit'))}</th>
              <th>{t(k('headerBehavior'))}</th>
            </tr>
          </thead>
          <tbody>
            {TIMING_ROWS.map((r, i) => {
              const behaviorKey = `${r.mode.toLowerCase()}${r.last5s}${r.limit}` // e.g. aiOnLimited
              return (
                <tr key={i}>
                  <td>{t(k(`mode${r.mode}`))}</td>
                  <td>{t(k(`state${r.last5s}`))}</td>
                  <td>{t(k(`limit${r.limit === 'Limited' ? 'Yes' : 'No'}`))}</td>
                  <td>{t(k(behaviorKey))}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </span>
  )
}

export default function Settings() {
  const { t } = useTranslation()
  const { activeAccount } = useAccounts()
  const accountId = activeAccount?.id ?? null
  const base = accountId ? `/api/accounts/${accountId}` : null
  const [courses, setCourses] = useState<CourseState[]>([])
  const [loading, setLoading] = useState(true)
  const [ai, setAi] = useState<AISettings>({ keys: [], active_key: -1, fallback_keys: true })
  const [newKey, setNewKey] = useState<AIKeyEntry>({ name: '', provider: 'qwen', key: '' })
  const [addingKey, setAddingKey] = useState(false)
  const [appliedAllFrom, setAppliedAllFrom] = useState<string | null>(null)
  const [defaults, setDefaults] = useState<CourseConfig | null>(null)
  const [pushdeer, setPushdeer] = useState<PushdeerSettings>({ keys: [], active_key: -1, language: 'zh' })
  const [newPushdeerKey, setNewPushdeerKey] = useState<PushdeerKeyEntry>({ name: '', endpoint: 'https://api2.pushdeer.com', push_key: '' })
  const [addingPushdeerKey, setAddingPushdeerKey] = useState(false)
  const [pushdeerTestStatus, setPushdeerTestStatus] = useState<Record<number, 'idle' | 'testing' | 'success' | 'error'>>({})
  const [pushdeerTestMessage, setPushdeerTestMessage] = useState<Record<number, string>>({})
  const [pollSettings, setPollSettings] = useState<PollIntervalSettings | null>(null)
  const [pollInput, setPollInput] = useState<string>('')
  const [pollSaveStatus, setPollSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const savedCoursesRef = useRef<Record<string, string>>({})

  const reloadAi = () => {
    if (!base) return Promise.resolve()
    return fetch(`${base}/ai/settings`).then((r) => r.json()).then(setAi).catch(() => { })
  }

  const reloadPushdeer = () => {
    if (!base) return Promise.resolve()
    return fetch(`${base}/pushdeer/settings`).then((r) => r.json()).then(setPushdeer).catch(() => { })
  }

  useEffect(() => {
    if (!base) {
      setLoading(false)
      return
    }
    setLoading(true)
    Promise.all([
      fetch(`${base}/courses/all`).then((r) => r.json()),
      fetch(`${base}/courses/settings`).then((r) => r.json()),
      fetch(`${base}/ai/settings`).then((r) => r.json()),
      fetch(`${base}/courses/defaults`).then((r) => r.json()),
      fetch(`${base}/pushdeer/settings`).then((r) => r.json()),
      fetch(`${base}/poll-interval`).then((r) => r.json()),
    ])
      .then(([allCourses, settings, aiSettings, defs, pd, poll]: [CourseItem[], CoursesMap, AISettings, CourseConfig, PushdeerSettings, PollIntervalSettings]) => {
        setDefaults(defs)
        const built = buildCourseStates(allCourses, settings, defs)
        setCourses(built)
        const snap: Record<string, string> = {}
        for (const c of built) snap[c.courseId] = courseFingerprint(c)
        savedCoursesRef.current = snap
        setAi(aiSettings)
        setPushdeer(pd)
        setPollSettings(poll)
        setPollInput(String(poll.poll_interval))
      })
      .catch(() => { })
      .finally(() => setLoading(false))
  }, [base])

  const handleSavePollInterval = async () => {
    if (!base || !pollSettings) return
    const parsed = parseInt(pollInput, 10)
    if (!Number.isFinite(parsed)) {
      setPollSaveStatus('error')
      return
    }
    const clamped = Math.max(pollSettings.min, Math.min(pollSettings.max, parsed))
    setPollSaveStatus('saving')
    try {
      const resp = await fetch(`${base}/poll-interval`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ poll_interval: clamped }),
      })
      if (!resp.ok) throw new Error('Save failed')
      const data: { ok: boolean; poll_interval: number } = await resp.json()
      setPollSettings({ ...pollSettings, poll_interval: data.poll_interval })
      setPollInput(String(data.poll_interval))
      setPollSaveStatus('saved')
      setTimeout(() => setPollSaveStatus('idle'), 2000)
    } catch {
      setPollSaveStatus('error')
    }
  }

  function courseFingerprint(c: CourseState): string {
    const { courseId: _, name: __, saveStatus: ___, ...rest } = c
    return JSON.stringify(rest)
  }

  function isDirty(course: CourseState): boolean {
    return courseFingerprint(course) !== savedCoursesRef.current[course.courseId]
  }

  const handleAddKey = async () => {
    if (!base || !newKey.name.trim() || !newKey.key.trim()) return
    setAddingKey(true)
    try {
      const resp = await fetch(`${base}/ai/keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newKey),
      })
      if (!resp.ok) throw new Error('Add failed')
      setNewKey({ name: '', provider: 'qwen', key: '' })
      await reloadAi()
    } catch { }
    setAddingKey(false)
  }

  const handleDeleteKey = async (index: number) => {
    if (!base) return
    await fetch(`${base}/ai/keys/${index}`, { method: 'DELETE' })
    await reloadAi()
  }

  const handleSetActiveKey = async (index: number) => {
    if (!base) return
    await fetch(`${base}/ai/active`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_key: index }),
    })
    await reloadAi()
  }

  const handleToggleFallback = async (enabled: boolean) => {
    if (!base) return
    await fetch(`${base}/ai/fallback`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fallback_keys: enabled }),
    })
    await reloadAi()
  }

  const handleAddPushdeerKey = async () => {
    if (!base || !newPushdeerKey.name.trim() || !newPushdeerKey.push_key.trim() || !newPushdeerKey.endpoint.trim()) return
    setAddingPushdeerKey(true)
    try {
      const resp = await fetch(`${base}/pushdeer/keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newPushdeerKey.name.trim(),
          endpoint: newPushdeerKey.endpoint.trim(),
          push_key: newPushdeerKey.push_key.trim(),
        }),
      })
      if (!resp.ok) throw new Error('Add failed')
      setNewPushdeerKey({ name: '', endpoint: 'https://api2.pushdeer.com', push_key: '' })
      await reloadPushdeer()
    } catch { }
    setAddingPushdeerKey(false)
  }

  const handleDeletePushdeerKey = async (index: number) => {
    if (!base) return
    await fetch(`${base}/pushdeer/keys/${index}`, { method: 'DELETE' })
    await reloadPushdeer()
  }

  const handleSetActivePushdeerKey = async (index: number) => {
    if (!base) return
    await fetch(`${base}/pushdeer/active`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_key: index }),
    })
    await reloadPushdeer()
  }

  const handleSetPushdeerLanguage = async (language: 'zh' | 'en') => {
    if (!base) return
    await fetch(`${base}/pushdeer/language`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ language }),
    })
    await reloadPushdeer()
  }

  const handleTestPushdeerKey = async (index: number) => {
    if (!base) return
    setPushdeerTestStatus((prev) => ({ ...prev, [index]: 'testing' }))
    setPushdeerTestMessage((prev) => ({ ...prev, [index]: '' }))
    try {
      const resp = await fetch(`${base}/pushdeer/test/${index}`, { method: 'POST' })
      const data: { ok: boolean; message: string } = await resp.json()
      setPushdeerTestStatus((prev) => ({ ...prev, [index]: data.ok ? 'success' : 'error' }))
      setPushdeerTestMessage((prev) => ({ ...prev, [index]: data.ok ? '' : (data.message || '') }))
      setTimeout(() => {
        setPushdeerTestStatus((prev) => ({ ...prev, [index]: 'idle' }))
        setPushdeerTestMessage((prev) => ({ ...prev, [index]: '' }))
      }, 4000)
    } catch (e) {
      setPushdeerTestStatus((prev) => ({ ...prev, [index]: 'error' }))
      setPushdeerTestMessage((prev) => ({ ...prev, [index]: String(e) }))
      setTimeout(() => {
        setPushdeerTestStatus((prev) => ({ ...prev, [index]: 'idle' }))
        setPushdeerTestMessage((prev) => ({ ...prev, [index]: '' }))
      }, 4000)
    }
  }

  const updateField = <K extends keyof CourseConfig>(
    courseId: string,
    field: K,
    value: CourseConfig[K]
  ) => {
    setCourses((prev) =>
      prev.map((c) =>
        c.courseId === courseId ? { ...c, [field]: value, saveStatus: 'idle' } : c
      )
    )
  }

  const handleSave = async (course: CourseState) => {
    setCourses((prev) =>
      prev.map((c) =>
        c.courseId === course.courseId ? { ...c, saveStatus: 'saving' } : c
      )
    )

    try {
      const resp = await fetch(`${base}/courses/settings/${course.courseId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type1: course.type1,
          type2: course.type2,
          type3: course.type3,
          type4: course.type4,
          type5: course.type5,
          course_enabled: course.course_enabled,
          answer_last5s: course.answer_last5s,
          auto_danmu: course.auto_danmu,
          auto_redpacket: course.auto_redpacket,
          danmu_threshold: course.danmu_threshold,
          notification: course.notification,
          voice_notification: course.voice_notification,
          pushdeer_notification: course.pushdeer_notification,
        }),
      })
      if (!resp.ok) throw new Error('Save failed')
      savedCoursesRef.current[course.courseId] = courseFingerprint(course)
      setCourses((prev) =>
        prev.map((c) =>
          c.courseId === course.courseId ? { ...c, saveStatus: 'saved' } : c
        )
      )
      setTimeout(() => {
        setCourses((prev) =>
          prev.map((c) =>
            c.courseId === course.courseId ? { ...c, saveStatus: 'idle' } : c
          )
        )
      }, 2000)
    } catch {
      setCourses((prev) =>
        prev.map((c) =>
          c.courseId === course.courseId ? { ...c, saveStatus: 'error' } : c
        )
      )
    }
  }

  const applyToAll = async (source: CourseState) => {
    const payload = {
      type1: source.type1,
      type2: source.type2,
      type3: source.type3,
      type4: source.type4,
      type5: source.type5,
      course_enabled: source.course_enabled,
      answer_last5s: source.answer_last5s,
      auto_danmu: source.auto_danmu,
      auto_redpacket: source.auto_redpacket,
      danmu_threshold: source.danmu_threshold,
      notification: source.notification,
      voice_notification: source.voice_notification,
      pushdeer_notification: source.pushdeer_notification,
    }
    const results = await Promise.all(
      courses.map((c) =>
        fetch(`${base}/courses/settings/${c.courseId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }).then((r) => r.ok)
      )
    )
    if (results.every(Boolean)) {
      setCourses((prev) => {
        const updated = prev.map((c) => ({
          ...c,
          ...payload,
          notification: { ...payload.notification },
          voice_notification: { ...payload.voice_notification },
          pushdeer_notification: { ...payload.pushdeer_notification },
          saveStatus: 'idle' as const,
        }))
        for (const c of updated) savedCoursesRef.current[c.courseId] = courseFingerprint(c)
        return updated
      })
      setAppliedAllFrom(source.courseId)
      setTimeout(() => setAppliedAllFrom(null), 2000)
    }
  }

  const resetToDefault = (courseId: string) => {
    if (!defaults) return
    setCourses((prev) =>
      prev.map((c) =>
        c.courseId === courseId
          ? {
            ...c,
            type1: defaults.type1,
            type2: defaults.type2,
            type3: defaults.type3,
            type4: defaults.type4,
            type5: defaults.type5,
            course_enabled: defaults.course_enabled,
            answer_last5s: defaults.answer_last5s,
            auto_danmu: defaults.auto_danmu,
            auto_redpacket: defaults.auto_redpacket,
            danmu_threshold: defaults.danmu_threshold,
            notification: { ...defaults.notification },
            voice_notification: { ...defaults.voice_notification },
            pushdeer_notification: { ...defaults.pushdeer_notification },
            saveStatus: 'idle',
          }
          : c
      )
    )
  }

  const choiceModes = [
    { value: 'ai', label: 'AI' },
    { value: 'random', label: t('settings.random') },
    { value: 'off', label: t('settings.disabled') },
  ]
  const voteModes = [
    { value: 'ai', label: 'AI' },
    { value: 'random', label: t('settings.random') },
    { value: 'off', label: t('settings.disabled') },
  ]
  const shortAnswerModes = [
    { value: 'ai', label: 'AI' },
    { value: 'blank', label: t('settings.blank') },
    { value: 'off', label: t('settings.disabled') },
  ]

  if (loading) {
    return (
      <div className="page">
        <p className="empty-message">{t('common.loading')}</p>
      </div>
    )
  }

  return (
    <div className="page">
      {/* AI Settings */}
      <section className="settings-section">
        <h2 className="settings-section-title">{t('settings.aiSettings')}</h2>

        <div className="card">
          {ai.keys.length > 0 && (
            <div className="credential-list">
              {ai.keys.map((entry, idx) => (
                <div key={idx} className={`credential-item ${idx === ai.active_key ? 'credential-active' : ''}`}>
                  <div className="credential-info">
                    <span className="credential-name">{entry.name}</span>
                    <span className="credential-meta">{PROVIDER_LABELS[entry.provider] ?? entry.provider}</span>
                    <span className="credential-masked">{entry.key}</span>
                  </div>
                  <div className="credential-actions">
                    <button
                      className={`btn btn-sm ${idx === ai.active_key ? 'btn-success' : 'btn-secondary'}`}
                      onClick={() => handleSetActiveKey(idx)}
                    >
                      {idx === ai.active_key ? t('settings.inUse') : t('settings.use')}
                    </button>
                    <button
                      className="btn btn-sm btn-danger"
                      onClick={() => handleDeleteKey(idx)}
                    >
                      {t('common.delete')}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {ai.keys.length > 1 && (
            <div className="form-row" style={{ padding: '0 16px', marginBottom: 8 }}>
              <label className="form-label">
                {t('settings.fallbackKeys')}
                <span className="tooltip-trigger" data-tooltip={t('settings.fallbackKeysDesc')}>?</span>
              </label>
              <div className="toggle-group">
                <button
                  className={`toggle-option ${ai.fallback_keys ? 'selected' : ''}`}
                  onClick={() => handleToggleFallback(true)}
                >
                  {t('common.on')}
                </button>
                <button
                  className={`toggle-option ${!ai.fallback_keys ? 'selected' : ''}`}
                  onClick={() => handleToggleFallback(false)}
                >
                  {t('common.off')}
                </button>
              </div>
            </div>
          )}

          <div className="credential-add-form">
            <div className="credential-add-fields">
              <input
                type="text"
                className="form-input"
                value={newKey.name}
                placeholder={t('settings.keyNamePlaceholder')}
                onChange={(e) => setNewKey({ ...newKey, name: e.target.value })}
              />
              <select
                className="form-select"
                value={newKey.provider}
                onChange={(e) => setNewKey({ ...newKey, provider: e.target.value })}
              >
                <option value="google">Google</option>
                <option value="qwen">ModelScope</option>
              </select>
              <input
                type="password"
                className="form-input"
                value={newKey.key}
                placeholder={t('settings.apiKeyPlaceholder')}
                onChange={(e) => setNewKey({ ...newKey, key: e.target.value })}
              />
            </div>
            <button
              className="btn btn-primary"
              onClick={handleAddKey}
              disabled={addingKey || !newKey.name.trim() || !newKey.key.trim()}
            >
              {addingKey ? t('settings.applying') : t('settings.addKey')}
            </button>
          </div>
          <p className="empty-message" style={{ padding: '12px 16px 0', margin: 0, fontSize: 12 }}>
            {t('common.betaWarning')}
          </p>
        </div>
      </section>

      {/* PushDeer Settings */}
      <section className="settings-section">
        <h2 className="settings-section-title">{t('settings.pushdeerSettings')}</h2>

        <div className="card">
          {pushdeer.keys.length > 0 && (
            <div className="credential-list">
              {pushdeer.keys.map((entry, idx) => {
                const status = pushdeerTestStatus[idx] ?? 'idle'
                const msg = pushdeerTestMessage[idx] ?? ''
                return (
                  <div key={idx} className={`credential-item ${idx === pushdeer.active_key ? 'credential-active' : ''}`}>
                    <div className="credential-info">
                      <span className="credential-name">{entry.name}</span>
                      <span className="credential-meta">{entry.endpoint}</span>
                      <span className="credential-masked">{entry.push_key}</span>
                    </div>
                    <div className="credential-actions">
                      <button
                        className={`btn btn-sm ${idx === pushdeer.active_key ? 'btn-success' : 'btn-secondary'}`}
                        onClick={() => handleSetActivePushdeerKey(idx)}
                      >
                        {idx === pushdeer.active_key ? t('settings.inUse') : t('settings.use')}
                      </button>
                      <button
                        className={`btn btn-sm ${status === 'success' ? 'btn-success' : status === 'error' ? 'btn-danger' : 'btn-secondary'}`}
                        onClick={() => handleTestPushdeerKey(idx)}
                        disabled={status === 'testing'}
                        title={msg || undefined}
                      >
                        {status === 'testing'
                          ? t('settings.pushdeerTesting')
                          : status === 'success'
                            ? t('settings.pushdeerTestSuccess')
                            : status === 'error'
                              ? t('settings.pushdeerTestFailed')
                              : t('settings.pushdeerTest')}
                      </button>
                      <button
                        className="btn btn-sm btn-danger"
                        onClick={() => handleDeletePushdeerKey(idx)}
                      >
                        {t('common.delete')}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {pushdeer.keys.length > 0 && (
            <div className="form-row" style={{ padding: '0 16px', marginBottom: 8 }}>
              <label className="form-label">{t('settings.pushdeerLanguage')}</label>
              <div className="toggle-group">
                <button
                  className={`toggle-option ${pushdeer.language === 'zh' ? 'selected' : ''}`}
                  onClick={() => handleSetPushdeerLanguage('zh')}
                >
                  中文
                </button>
                <button
                  className={`toggle-option ${pushdeer.language === 'en' ? 'selected' : ''}`}
                  onClick={() => handleSetPushdeerLanguage('en')}
                >
                  English
                </button>
              </div>
            </div>
          )}

          <div className="credential-add-form">
            <div className="credential-add-fields">
              <input
                type="text"
                className="form-input"
                value={newPushdeerKey.name}
                placeholder={t('settings.keyNamePlaceholder')}
                onChange={(e) => setNewPushdeerKey({ ...newPushdeerKey, name: e.target.value })}
              />
              <input
                type="text"
                className="form-input"
                value={newPushdeerKey.endpoint}
                placeholder={t('settings.pushdeerEndpointPlaceholder')}
                onChange={(e) => setNewPushdeerKey({ ...newPushdeerKey, endpoint: e.target.value })}
              />
              <input
                type="password"
                className="form-input"
                value={newPushdeerKey.push_key}
                placeholder={t('settings.apiKeyPlaceholder')}
                onChange={(e) => setNewPushdeerKey({ ...newPushdeerKey, push_key: e.target.value })}
              />
            </div>
            <button
              className="btn btn-primary"
              onClick={handleAddPushdeerKey}
              disabled={addingPushdeerKey || !newPushdeerKey.name.trim() || !newPushdeerKey.push_key.trim() || !newPushdeerKey.endpoint.trim()}
            >
              {addingPushdeerKey ? t('settings.applying') : t('settings.addKey')}
            </button>
          </div>
          <p className="empty-message" style={{ padding: '12px 16px 0', margin: 0, fontSize: 12 }}>
            {t('settings.pushdeerEndpointHint')}
          </p>
        </div>
      </section>

      {/* Monitor Settings */}
      {pollSettings && (
        <section className="settings-section">
          <h2 className="settings-section-title">{t('settings.monitorSettings')}</h2>
          <div className="card">
            <div className="form-row" style={{ padding: '12px 16px' }}>
              <label className="form-label">
                {t('settings.pollInterval')}
                <span
                  className="tooltip-trigger"
                  data-tooltip={t('settings.pollIntervalDesc', { min: pollSettings.min, max: pollSettings.max, default: pollSettings.default })}
                >
                  ?
                </span>
              </label>
              <div className="input-with-unit" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input
                  type="number"
                  className="form-input-number"
                  min={pollSettings.min}
                  max={pollSettings.max}
                  value={pollInput}
                  onChange={(e) => {
                    setPollInput(e.target.value)
                    setPollSaveStatus('idle')
                  }}
                />
                <span className="input-unit">{t('settings.seconds')}</span>
                <button
                  className={`btn btn-sm ${pollSaveStatus === 'saved' ? 'btn-success' : pollSaveStatus === 'error' ? 'btn-danger' : 'btn-primary'}`}
                  onClick={handleSavePollInterval}
                  disabled={pollSaveStatus === 'saving' || pollInput === String(pollSettings.poll_interval)}
                >
                  {pollSaveStatus === 'saving'
                    ? t('settings.applying')
                    : pollSaveStatus === 'saved'
                      ? t('settings.applied')
                      : t('settings.apply')}
                </button>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Course Settings */}
      <section className="settings-section">
        <h2 className="settings-section-title">{t('settings.title')}</h2>

        {courses.length === 0 ? (
          <div className="card">
            <p className="empty-message">{t('settings.noCourses')}</p>
          </div>
        ) : (
          <div className="course-grid">
            {courses.map((course) => (
              <div key={course.courseId} className="course-card">
                <div className="course-card-header">
                  <h3 className="course-card-title">
                    {course.name || course.courseId}
                  </h3>
                </div>

                <div className="course-card-body">
                  {/* Course toggle */}
                  <div className="settings-group">
                    <span className="settings-group-label">{t('settings.courseGroup')}</span>
                    <div className="form-row">
                      <label className="form-label">
                        {t('settings.courseEnabled')}
                        <span className="tooltip-trigger" data-tooltip={t('settings.courseEnabledDesc')}>?</span>
                      </label>
                      <div className="toggle-group">
                        <button
                          className={`toggle-option ${course.course_enabled ? 'selected' : ''}`}
                          onClick={() => updateField(course.courseId, 'course_enabled', true)}
                        >
                          {t('common.on')}
                        </button>
                        <button
                          className={`toggle-option ${!course.course_enabled ? 'selected' : ''}`}
                          onClick={() => updateField(course.courseId, 'course_enabled', false)}
                        >
                          {t('common.off')}
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Quiz Modes */}
                  <div className="settings-group">
                    <span className="settings-group-label">{t('settings.quizModes')}</span>
                    <QuizModeSelect
                      label={t('events.problemType1')}
                      value={course.type1}
                      options={choiceModes}
                      onChange={(v) => updateField(course.courseId, 'type1', v)}
                    />
                    <QuizModeSelect
                      label={t('events.problemType2')}
                      value={course.type2}
                      options={choiceModes}
                      onChange={(v) => updateField(course.courseId, 'type2', v)}
                    />
                    <QuizModeSelect
                      label={t('events.problemType3')}
                      value={course.type3}
                      options={voteModes}
                      onChange={(v) => updateField(course.courseId, 'type3', v)}
                    />
                    <div className="form-row">
                      <label className="form-label">{t('events.problemType4')}</label>
                      <span className="badge badge-gray">{t('settings.reserved')}</span>
                    </div>
                    <QuizModeSelect
                      label={t('events.problemType5')}
                      value={course.type5}
                      options={shortAnswerModes}
                      onChange={(v) => updateField(course.courseId, 'type5', v)}
                    />
                  </div>

                  {/* Timing */}
                  <div className="settings-group">
                    <span className="settings-group-label">{t('settings.timing')}</span>
                    <div className="form-row">
                      <label className="form-label">
                        {t('settings.answerLast5s')}
                        <TimingTooltip />
                      </label>
                      <div className="toggle-group">
                        <button
                          className={`toggle-option ${course.answer_last5s ? 'selected' : ''}`}
                          onClick={() => updateField(course.courseId, 'answer_last5s', true)}
                        >
                          {t('common.on')}
                        </button>
                        <button
                          className={`toggle-option ${!course.answer_last5s ? 'selected' : ''}`}
                          onClick={() => updateField(course.courseId, 'answer_last5s', false)}
                        >
                          {t('common.off')}
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Danmu */}
                  <div className="settings-group">
                    <span className="settings-group-label">{t('settings.danmu')}</span>
                    <div className="form-row">
                      <label className="form-label">{t('settings.autoDanmu')}</label>
                      <div className="toggle-group">
                        <button
                          className={`toggle-option ${course.auto_danmu ? 'selected' : ''}`}
                          onClick={() => updateField(course.courseId, 'auto_danmu', true)}
                        >
                          {t('common.yes')}
                        </button>
                        <button
                          className={`toggle-option ${!course.auto_danmu ? 'selected' : ''}`}
                          onClick={() => updateField(course.courseId, 'auto_danmu', false)}
                        >
                          {t('common.no')}
                        </button>
                      </div>
                    </div>
                    {course.auto_danmu && (
                      <div className="form-row form-row-sub">
                        <label className="form-label">{t('settings.danmuThreshold')}</label>
                        <div className="input-with-unit">
                          <input
                            type="number"
                            className="form-input-number"
                            min={1}
                            max={99}
                            value={course.danmu_threshold}
                            onChange={(e) =>
                              updateField(course.courseId, 'danmu_threshold', Math.max(1, parseInt(e.target.value) || 1))
                            }
                          />
                          <span className="input-unit">{t('settings.times')}</span>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Red Packet */}
                  <div className="settings-group">
                    <span className="settings-group-label">{t('settings.redPacket')}</span>
                    <div className="form-row">
                      <label className="form-label">{t('settings.autoRedpacket')}</label>
                      <div className="toggle-group">
                        <button
                          className={`toggle-option ${course.auto_redpacket ? 'selected' : ''}`}
                          onClick={() => updateField(course.courseId, 'auto_redpacket', true)}
                        >
                          {t('common.yes')}
                        </button>
                        <button
                          className={`toggle-option ${!course.auto_redpacket ? 'selected' : ''}`}
                          onClick={() => updateField(course.courseId, 'auto_redpacket', false)}
                        >
                          {t('common.no')}
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Notifications */}
                  <div className="settings-group">
                    <span className="settings-group-label">{t('settings.notifications')}</span>
                    <NotificationSection
                      label={t('settings.notification')}
                      value={course.notification}
                      onChange={(v) => updateField(course.courseId, 'notification', v)}
                    />
                    <NotificationSection
                      label={t('settings.voiceNotification')}
                      value={course.voice_notification}
                      onChange={(v) => updateField(course.courseId, 'voice_notification', v)}
                    />
                    <NotificationSection
                      label={t('settings.pushdeerNotification')}
                      value={course.pushdeer_notification}
                      onChange={(v) => updateField(course.courseId, 'pushdeer_notification', v)}
                      disabled={pushdeer.keys.length === 0}
                      disabledTitle={t('settings.pushdeerNoKey')}
                    />
                  </div>
                </div>

                <div className="course-card-footer">
                  <button
                    className="btn btn-ghost"
                    onClick={() => resetToDefault(course.courseId)}
                  >
                    {t('settings.default')}
                  </button>
                  <div className="footer-spacer" />
                  {courses.length > 1 && (
                    <button
                      className={`btn ${appliedAllFrom === course.courseId ? 'btn-success' : 'btn-secondary'}`}
                      onClick={() => applyToAll(course)}
                      disabled={appliedAllFrom !== null}
                    >
                      {appliedAllFrom === course.courseId ? t('settings.applied') : t('settings.applyToAll')}
                    </button>
                  )}
                  <button
                    className={`btn ${course.saveStatus === 'saved'
                      ? 'btn-success'
                      : course.saveStatus === 'error'
                        ? 'btn-danger'
                        : 'btn-primary'
                      }`}
                    onClick={() => handleSave(course)}
                    disabled={course.saveStatus === 'saving' || !isDirty(course)}
                  >
                    {course.saveStatus === 'saving'
                      ? t('settings.applying')
                      : course.saveStatus === 'saved'
                        ? t('settings.applied')
                        : t('settings.apply')}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
