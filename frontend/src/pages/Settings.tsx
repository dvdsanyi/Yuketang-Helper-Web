import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { NotificationSub, CourseItem } from '../types'

interface CourseConfig {
  name: string
  type1: string
  type2: string
  type3: string
  type4: string
  type5: string
  answer_delay_min: number
  answer_delay_max: number
  auto_danmu: boolean
  danmu_threshold: number
  notification: NotificationSub
  voice_notification: NotificationSub
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

interface AISettings {
  keys: AIKeyEntry[]
  active_key: number
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
      answer_delay_min: cfg.answer_delay_min ?? defaults.answer_delay_min,
      answer_delay_max: cfg.answer_delay_max ?? defaults.answer_delay_max,
      auto_danmu: cfg.auto_danmu ?? defaults.auto_danmu,
      danmu_threshold: cfg.danmu_threshold ?? defaults.danmu_threshold,
      notification: { ...defaults.notification, ...cfg.notification },
      voice_notification: { ...defaults.voice_notification, ...cfg.voice_notification },
      saveStatus: 'idle',
    }
  })
}

function NotificationSection({
  label,
  value,
  onChange,
}: {
  label: string
  value: NotificationSub
  onChange: (v: NotificationSub) => void
}) {
  const { t } = useTranslation()
  const subKeys: (keyof Omit<NotificationSub, 'enabled'>)[] = ['signin', 'problem', 'call', 'danmu']

  return (
    <div className="notif-section">
      <div className="form-row">
        <label className="form-label">{label}</label>
        <div className="toggle-group">
          <button
            className={`toggle-option ${value.enabled ? 'selected' : ''}`}
            onClick={() => onChange({ ...value, enabled: true })}
          >
            {t('common.on')}
          </button>
          <button
            className={`toggle-option ${!value.enabled ? 'selected' : ''}`}
            onClick={() => onChange({ ...value, enabled: false })}
          >
            {t('common.off')}
          </button>
        </div>
      </div>
      {value.enabled && (
        <div className="notif-suboptions">
          {subKeys.map((key) => (
            <label key={key} className="notif-sub-item">
              <input
                type="checkbox"
                checked={value[key]}
                onChange={(e) => onChange({ ...value, [key]: e.target.checked })}
              />
              <span>{t(`settings.notif_${key}`)}</span>
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

export default function Settings() {
  const { t } = useTranslation()
  const [courses, setCourses] = useState<CourseState[]>([])
  const [loading, setLoading] = useState(true)
  const [ai, setAi] = useState<AISettings>({ keys: [], active_key: -1 })
  const [newKey, setNewKey] = useState<AIKeyEntry>({ name: '', provider: 'google', key: '' })
  const [addingKey, setAddingKey] = useState(false)
  const [appliedAllFrom, setAppliedAllFrom] = useState<string | null>(null)
  const [defaults, setDefaults] = useState<CourseConfig | null>(null)
  const savedCoursesRef = useRef<Record<string, string>>({})

  const reloadAi = () =>
    fetch('/api/ai/settings').then((r) => r.json()).then(setAi).catch(() => {})

  useEffect(() => {
    Promise.all([
      fetch('/api/courses/all').then((r) => r.json()),
      fetch('/api/courses/settings').then((r) => r.json()),
      fetch('/api/ai/settings').then((r) => r.json()),
      fetch('/api/courses/defaults').then((r) => r.json()),
    ])
      .then(([allCourses, settings, aiSettings, defs]: [CourseItem[], CoursesMap, AISettings, CourseConfig]) => {
        setDefaults(defs)
        const built = buildCourseStates(allCourses, settings, defs)
        setCourses(built)
        const snap: Record<string, string> = {}
        for (const c of built) snap[c.courseId] = courseFingerprint(c)
        savedCoursesRef.current = snap
        setAi(aiSettings)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function courseFingerprint(c: CourseState): string {
    const { courseId: _, name: __, saveStatus: ___, ...rest } = c
    return JSON.stringify(rest)
  }

  function isDirty(course: CourseState): boolean {
    return courseFingerprint(course) !== savedCoursesRef.current[course.courseId]
  }

  const handleAddKey = async () => {
    if (!newKey.name.trim() || !newKey.key.trim()) return
    setAddingKey(true)
    try {
      const resp = await fetch('/api/ai/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newKey),
      })
      if (!resp.ok) throw new Error('Add failed')
      setNewKey({ name: '', provider: 'google', key: '' })
      await reloadAi()
    } catch {}
    setAddingKey(false)
  }

  const handleDeleteKey = async (index: number) => {
    await fetch(`/api/ai/keys/${index}`, { method: 'DELETE' })
    await reloadAi()
  }

  const handleSetActiveKey = async (index: number) => {
    await fetch('/api/ai/active', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_key: index }),
    })
    await reloadAi()
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
      const resp = await fetch(`/api/courses/settings/${course.courseId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type1: course.type1,
          type2: course.type2,
          type3: course.type3,
          type4: course.type4,
          type5: course.type5,
          answer_delay_min: course.answer_delay_min,
          answer_delay_max: course.answer_delay_max,
          auto_danmu: course.auto_danmu,
          danmu_threshold: course.danmu_threshold,
          notification: course.notification,
          voice_notification: course.voice_notification,
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
      answer_delay_min: source.answer_delay_min,
      answer_delay_max: source.answer_delay_max,
      auto_danmu: source.auto_danmu,
      danmu_threshold: source.danmu_threshold,
      notification: source.notification,
      voice_notification: source.voice_notification,
    }
    const results = await Promise.all(
      courses.map((c) =>
        fetch(`/api/courses/settings/${c.courseId}`, {
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
              answer_delay_min: defaults.answer_delay_min,
              answer_delay_max: defaults.answer_delay_max,
              auto_danmu: defaults.auto_danmu,
              danmu_threshold: defaults.danmu_threshold,
              notification: { ...defaults.notification },
              voice_notification: { ...defaults.voice_notification },
              saveStatus: 'idle',
            }
          : c
      )
    )
  }

  const modeOptions = (hasAi: boolean) => {
    const opts = [
      { value: 'random', label: t('settings.random') },
      { value: 'off', label: t('settings.disabled') },
    ]
    if (hasAi) opts.splice(1, 0, { value: 'ai', label: 'AI' })
    return opts
  }

  if (loading) {
    return (
      <div className="page">
        <h1 className="page-title">{t('settings.title')}</h1>
        <p className="empty-message">{t('common.loading')}</p>
      </div>
    )
  }

  return (
    <div className="page">
      <h1 className="page-title">{t('settings.title')}</h1>

      {/* AI Settings */}
      <section className="settings-section">
        <h2 className="settings-section-title">{t('settings.aiSettings')}</h2>

        <div className="card">
          {ai.keys.length > 0 && (
            <div className="ai-key-list">
              {ai.keys.map((entry, idx) => (
                <div key={idx} className={`ai-key-item ${idx === ai.active_key ? 'ai-key-active' : ''}`}>
                  <div className="ai-key-info">
                    <span className="ai-key-name">{entry.name}</span>
                    <span className="ai-key-provider">{entry.provider}</span>
                    <span className="ai-key-masked">{entry.key}</span>
                  </div>
                  <div className="ai-key-actions">
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
                      {t('settings.delete')}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="ai-add-form">
            <div className="ai-add-fields">
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
        </div>
      </section>

      {/* Course Settings */}
      <section className="settings-section">
        <h2 className="settings-section-title">{t('settings.courseSettings')}</h2>

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
                  {/* Quiz Modes */}
                  <div className="settings-group">
                    <span className="settings-group-label">{t('settings.quizModes')}</span>
                    <QuizModeSelect
                      label={t('settings.type1')}
                      value={course.type1}
                      options={modeOptions(true)}
                      onChange={(v) => updateField(course.courseId, 'type1', v)}
                    />
                    <QuizModeSelect
                      label={t('settings.type2')}
                      value={course.type2}
                      options={modeOptions(true)}
                      onChange={(v) => updateField(course.courseId, 'type2', v)}
                    />
                    <QuizModeSelect
                      label={t('settings.type3')}
                      value={course.type3}
                      options={modeOptions(false)}
                      onChange={(v) => updateField(course.courseId, 'type3', v)}
                    />
                    <div className="form-row">
                      <label className="form-label">{t('settings.type4')}</label>
                      <span className="badge badge-gray">{t('settings.reserved')}</span>
                    </div>
                    <QuizModeSelect
                      label={t('settings.type5')}
                      value={course.type5}
                      options={[
                        { value: 'ai', label: 'AI' },
                        { value: 'off', label: t('settings.disabled') },
                      ]}
                      onChange={(v) => updateField(course.courseId, 'type5', v)}
                    />
                  </div>

                  {/* Timing */}
                  <div className="settings-group">
                    <span className="settings-group-label">{t('settings.timing')}</span>
                    <div className="form-row">
                      <label className="form-label">{t('settings.answerDelay')}</label>
                      <div className="input-with-unit">
                        <input
                          type="number"
                          className="form-input-number"
                          min={1}
                          max={course.answer_delay_max - 1}
                          value={course.answer_delay_min}
                          onChange={(e) => {
                            const val = Math.max(1, parseInt(e.target.value) || 1)
                            updateField(course.courseId, 'answer_delay_min', val)
                            if (val >= course.answer_delay_max) {
                              updateField(course.courseId, 'answer_delay_max', val + 1)
                            }
                          }}
                        />
                        <span className="input-unit">{t('settings.to')}</span>
                        <input
                          type="number"
                          className="form-input-number"
                          min={course.answer_delay_min + 1}
                          max={300}
                          value={course.answer_delay_max}
                          onChange={(e) => {
                            const val = Math.max(course.answer_delay_min + 1, parseInt(e.target.value) || course.answer_delay_min + 1)
                            updateField(course.courseId, 'answer_delay_max', val)
                          }}
                        />
                        <span className="input-unit">{t('settings.seconds')}</span>
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
