import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type {
  ChoiceAnswerMode,
  CourseConfig,
  CourseItem,
  CourseSettings,
  CoursesMap,
  NotificationSub,
} from '../../types'

// The three notification "channels" share the same NotificationSub schema;
// only the label and (for PushDeer) the gated-disabled rule differ.
const NOTIF_CHANNELS = [
  { field: 'notification', labelKey: 'settings.notification' },
  { field: 'voice_notification', labelKey: 'settings.voiceNotification' },
  { field: 'pushdeer_notification', labelKey: 'settings.pushdeerNotification' },
] as const satisfies ReadonlyArray<{
  field: 'notification' | 'voice_notification' | 'pushdeer_notification'
  labelKey: string
}>

// Strip the UI-only `name` field so the payload matches backend CourseSettingsModel.
function toPayload(c: CourseConfig | CourseSettings): CourseSettings {
  const {
    type1, type2, type3, type4, type5,
    course_enabled, answer_last5s, auto_danmu, auto_redpacket,
    danmu_threshold, notification, voice_notification, pushdeer_notification,
  } = c
  return {
    type1, type2, type3, type4, type5,
    course_enabled, answer_last5s, auto_danmu, auto_redpacket,
    danmu_threshold, notification, voice_notification, pushdeer_notification,
  }
}

function fingerprint(c: CourseConfig): string {
  return JSON.stringify(toPayload(c))
}

interface CourseState extends CourseConfig {
  courseId: string
  saveStatus: 'idle' | 'saving' | 'saved' | 'error'
}

function buildCourseStates(
  allCourses: CourseItem[],
  settings: CoursesMap,
  defaults: CourseSettings,
): CourseState[] {
  return allCourses.map((c) => {
    const cfg = settings[c.classroom_id] ?? ({} as Partial<CourseConfig>)
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

function QuizModeSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div className="form-row">
      <label className="form-label">{label}</label>
      <select className="form-select" value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  )
}

// Full Cartesian product of (mode × answer_last5s × limit) with the resulting
// submission behavior. Mirrors backend/lesson.py:_submit_window. Random and
// Blank share identical timing logic, so they share a single "Fallback" row
// group.
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

export default function CoursesSection({
  base,
  allCourses,
  settings,
  defaults,
  pushdeerKeyCount,
}: {
  base: string
  allCourses: CourseItem[]
  settings: CoursesMap
  defaults: CourseSettings
  pushdeerKeyCount: number
}) {
  const { t } = useTranslation()
  const [courses, setCourses] = useState<CourseState[]>(() => buildCourseStates(allCourses, settings, defaults))
  type ApplyAll = { courseId: string; status: 'success' | 'error' }
  const [appliedAll, setAppliedAll] = useState<ApplyAll | null>(null)
  const savedRef = useRef<Record<string, string>>({})

  // Re-seed when the upstream data changes (e.g. account switch).
  useEffect(() => {
    const built = buildCourseStates(allCourses, settings, defaults)
    setCourses(built)
    const snap: Record<string, string> = {}
    for (const c of built) snap[c.courseId] = fingerprint(c)
    savedRef.current = snap
  }, [allCourses, settings, defaults])

  const isDirty = (course: CourseState) => fingerprint(course) !== savedRef.current[course.courseId]

  const updateField = <K extends keyof CourseConfig>(
    courseId: string,
    field: K,
    value: CourseConfig[K],
  ) => {
    setCourses((prev) =>
      prev.map((c) =>
        c.courseId === courseId ? { ...c, [field]: value, saveStatus: 'idle' } : c,
      ),
    )
  }

  const patchCourse = (courseId: string, patch: Partial<CourseState>) =>
    setCourses((prev) => prev.map((c) => (c.courseId === courseId ? { ...c, ...patch } : c)))

  const handleSave = async (course: CourseState) => {
    patchCourse(course.courseId, { saveStatus: 'saving' })
    try {
      const resp = await fetch(`${base}/courses/settings/${course.courseId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toPayload(course)),
      })
      if (!resp.ok) throw new Error('Save failed')
      savedRef.current[course.courseId] = fingerprint(course)
      patchCourse(course.courseId, { saveStatus: 'saved' })
      setTimeout(() => patchCourse(course.courseId, { saveStatus: 'idle' }), 2000)
    } catch {
      patchCourse(course.courseId, { saveStatus: 'error' })
    }
  }

  const applyToAll = async (source: CourseState) => {
    const payload = toPayload(source)
    const results = await Promise.all(
      courses.map((c) =>
        fetch(`${base}/courses/settings/${c.courseId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
          .then((r) => r.ok)
          .catch(() => false),
      ),
    )
    setCourses((prev) => {
      const updated = prev.map((c, i) => {
        if (!results[i]) {
          // Save failed — leave fields untouched and mark the row so the
          // user can see which course(s) didn't accept the broadcast.
          return { ...c, saveStatus: 'error' as const }
        }
        const next: CourseState = {
          ...c,
          ...payload,
          notification: { ...payload.notification },
          voice_notification: { ...payload.voice_notification },
          pushdeer_notification: { ...payload.pushdeer_notification },
          saveStatus: 'idle',
        }
        savedRef.current[c.courseId] = fingerprint(next)
        return next
      })
      return updated
    })
    const allOk = results.every(Boolean)
    setAppliedAll({ courseId: source.courseId, status: allOk ? 'success' : 'error' })
    setTimeout(() => setAppliedAll(null), 2000)
  }

  const resetToDefault = (courseId: string) => {
    setCourses((prev) =>
      prev.map((c) =>
        c.courseId === courseId
          ? {
              ...c,
              ...toPayload(defaults),
              notification: { ...defaults.notification },
              voice_notification: { ...defaults.voice_notification },
              pushdeer_notification: { ...defaults.pushdeer_notification },
              saveStatus: 'idle',
            }
          : c,
      ),
    )
  }

  const choiceModes: { value: ChoiceAnswerMode; label: string }[] = [
    { value: 'ai', label: 'AI' },
    { value: 'random', label: t('settings.random') },
    { value: 'off', label: t('settings.disabled') },
  ]
  const shortAnswerModes: { value: CourseConfig['type5']; label: string }[] = [
    { value: 'ai', label: 'AI' },
    { value: 'blank', label: t('settings.blank') },
    { value: 'off', label: t('settings.disabled') },
  ]

  const saveButtonClass = (s: CourseState['saveStatus']) =>
    s === 'saved' ? 'btn-success'
    : s === 'error' ? 'btn-danger'
    : 'btn-primary'

  return (
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
                <h3 className="course-card-title">{course.name || course.courseId}</h3>
              </div>

              <div className="course-card-body">
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

                <div className="settings-group">
                  <span className="settings-group-label">{t('settings.quizModes')}</span>
                  {([1, 2, 3] as const).map((n) => {
                    const field = `type${n}` as const
                    return (
                      <QuizModeSelect
                        key={n}
                        label={t(`events.problemType${n}`)}
                        value={course[field]}
                        options={choiceModes}
                        onChange={(v) => updateField(course.courseId, field, v)}
                      />
                    )
                  })}
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
                            updateField(course.courseId, 'danmu_threshold', Math.max(1, parseInt(e.target.value, 10) || 1))
                          }
                        />
                        <span className="input-unit">{t('settings.times')}</span>
                      </div>
                    </div>
                  )}
                </div>

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

                <div className="settings-group">
                  <span className="settings-group-label">{t('settings.notifications')}</span>
                  {NOTIF_CHANNELS.map((ch) => (
                    <NotificationSection
                      key={ch.field}
                      label={t(ch.labelKey)}
                      value={course[ch.field]}
                      onChange={(v) => updateField(course.courseId, ch.field, v)}
                      disabled={ch.field === 'pushdeer_notification' && pushdeerKeyCount === 0}
                      disabledTitle={ch.field === 'pushdeer_notification' ? t('settings.pushdeerNoKey') : undefined}
                    />
                  ))}
                </div>
              </div>

              <div className="course-card-footer">
                <button className="btn btn-ghost" onClick={() => resetToDefault(course.courseId)}>
                  {t('settings.default')}
                </button>
                <div className="footer-spacer" />
                {courses.length > 1 && (() => {
                  const isThis = appliedAll?.courseId === course.courseId
                  const cls = isThis
                    ? (appliedAll?.status === 'success' ? 'btn-success' : 'btn-danger')
                    : 'btn-secondary'
                  const label = isThis
                    ? (appliedAll?.status === 'success' ? t('settings.applied') : t('settings.applyToAllFailed'))
                    : t('settings.applyToAll')
                  return (
                    <button
                      className={`btn ${cls}`}
                      onClick={() => applyToAll(course)}
                      disabled={appliedAll !== null}
                    >
                      {label}
                    </button>
                  )
                })()}
                <button
                  className={`btn ${saveButtonClass(course.saveStatus)}`}
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
  )
}
