import { useEffect, useState } from 'react'
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

const DEFAULT_NOTIF: NotificationSub = {
  enabled: true,
  signin: true,
  problem: true,
  call: true,
  danmu: false,
}

function buildCourseStates(allCourses: CourseItem[], settings: CoursesMap): CourseState[] {
  return allCourses.map((c) => {
    const cfg = settings[c.classroom_id] ?? {} as Partial<CourseConfig>
    return {
      courseId: c.classroom_id,
      name: c.name,
      type1: cfg.type1 ?? 'random',
      type2: cfg.type2 ?? 'random',
      type3: cfg.type3 ?? 'random',
      type4: cfg.type4 ?? 'off',
      type5: cfg.type5 ?? 'off',
      answer_delay_min: cfg.answer_delay_min ?? 3,
      answer_delay_max: cfg.answer_delay_max ?? 10,
      auto_danmu: cfg.auto_danmu ?? true,
      danmu_threshold: cfg.danmu_threshold ?? 3,
      notification: { ...DEFAULT_NOTIF, ...cfg.notification },
      voice_notification: { ...DEFAULT_NOTIF, ...cfg.voice_notification, enabled: cfg.voice_notification?.enabled ?? false },
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

export default function Settings() {
  const { t } = useTranslation()
  const [courses, setCourses] = useState<CourseState[]>([])
  const [loading, setLoading] = useState(true)
  const [ai, setAi] = useState<AISettings>({ keys: [], active_key: -1 })
  const [newKey, setNewKey] = useState<AIKeyEntry>({ name: '', provider: 'gemini', key: '' })
  const [addingKey, setAddingKey] = useState(false)

  const reloadAi = () =>
    fetch('/api/ai/settings').then((r) => r.json()).then(setAi).catch(() => {})

  useEffect(() => {
    Promise.all([
      fetch('/api/courses/all').then((r) => r.json()),
      fetch('/api/courses/settings').then((r) => r.json()),
      fetch('/api/ai/settings').then((r) => r.json()),
    ])
      .then(([allCourses, settings, aiSettings]: [CourseItem[], CoursesMap, AISettings]) => {
        setCourses(buildCourseStates(allCourses, settings))
        setAi(aiSettings)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

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
      setNewKey({ name: '', provider: 'gemini', key: '' })
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
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="course-card-header">
          <h3 className="course-card-title">{t('settings.aiSettings')}</h3>
        </div>
        <div className="course-card-body">
          {/* Saved keys */}
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

          {/* Add new key */}
          <div className="ai-add-key">
            <div className="form-row">
              <label className="form-label">{t('settings.keyName')}</label>
              <input
                type="text"
                className="form-input"
                value={newKey.name}
                placeholder={t('settings.keyNamePlaceholder')}
                onChange={(e) => setNewKey({ ...newKey, name: e.target.value })}
              />
            </div>
            <div className="form-row">
              <label className="form-label">{t('settings.aiProvider')}</label>
              <select
                className="form-select"
                value={newKey.provider}
                onChange={(e) => setNewKey({ ...newKey, provider: e.target.value })}
              >
                <option value="gemini">Gemini</option>
              </select>
            </div>
            <div className="form-row">
              <label className="form-label">{t('settings.apiKey')}</label>
              <input
                type="password"
                className="form-input"
                value={newKey.key}
                placeholder={t('settings.apiKeyPlaceholder')}
                onChange={(e) => setNewKey({ ...newKey, key: e.target.value })}
              />
            </div>
          </div>
        </div>
        <div className="course-card-footer">
          <button
            className="btn btn-primary"
            onClick={handleAddKey}
            disabled={addingKey || !newKey.name.trim() || !newKey.key.trim()}
          >
            {addingKey ? t('settings.saving') : t('settings.addKey')}
          </button>
        </div>
      </div>

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
                {/* Type 1: Single Choice */}
                <div className="form-row">
                  <label className="form-label">{t('settings.type1')}</label>
                  <select
                    className="form-select"
                    value={course.type1}
                    onChange={(e) => updateField(course.courseId, 'type1', e.target.value)}
                  >
                    <option value="random">{t('settings.random')}</option>
                    <option value="ai">AI</option>
                    <option value="off">{t('settings.disabled')}</option>
                  </select>
                </div>

                {/* Type 2: Multiple Choice */}
                <div className="form-row">
                  <label className="form-label">{t('settings.type2')}</label>
                  <select
                    className="form-select"
                    value={course.type2}
                    onChange={(e) => updateField(course.courseId, 'type2', e.target.value)}
                  >
                    <option value="random">{t('settings.random')}</option>
                    <option value="ai">AI</option>
                    <option value="off">{t('settings.disabled')}</option>
                  </select>
                </div>

                {/* Type 3: Vote */}
                <div className="form-row">
                  <label className="form-label">{t('settings.type3')}</label>
                  <select
                    className="form-select"
                    value={course.type3}
                    onChange={(e) => updateField(course.courseId, 'type3', e.target.value)}
                  >
                    <option value="random">{t('settings.random')}</option>
                    <option value="off">{t('settings.disabled')}</option>
                  </select>
                </div>

                {/* Type 4: Fill-in-blank (reserved) */}
                <div className="form-row">
                  <label className="form-label">{t('settings.type4')}</label>
                  <span className="badge badge-gray">{t('settings.reserved')}</span>
                </div>

                {/* Type 5: Short Answer */}
                <div className="form-row">
                  <label className="form-label">{t('settings.type5')}</label>
                  <select
                    className="form-select"
                    value={course.type5}
                    onChange={(e) => updateField(course.courseId, 'type5', e.target.value)}
                  >
                    <option value="ai">AI</option>
                    <option value="off">{t('settings.disabled')}</option>
                  </select>
                </div>

                {/* Answer Delay */}
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

                {/* Auto Danmu */}
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

                {/* Danmu Threshold */}
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

                {/* Notification */}
                <NotificationSection
                  label={t('settings.notification')}
                  value={course.notification}
                  onChange={(v) => updateField(course.courseId, 'notification', v)}
                />

                {/* Voice Notification */}
                <NotificationSection
                  label={t('settings.voiceNotification')}
                  value={course.voice_notification}
                  onChange={(v) => updateField(course.courseId, 'voice_notification', v)}
                />
              </div>

              <div className="course-card-footer">
                <button
                  className={`btn ${course.saveStatus === 'saved'
                      ? 'btn-success'
                      : course.saveStatus === 'error'
                        ? 'btn-danger'
                        : 'btn-primary'
                    }`}
                  onClick={() => handleSave(course)}
                  disabled={course.saveStatus === 'saving'}
                >
                  {course.saveStatus === 'saving'
                    ? t('settings.saving')
                    : course.saveStatus === 'saved'
                      ? t('settings.saved')
                      : t('settings.save')}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
