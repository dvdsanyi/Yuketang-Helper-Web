import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type {
  AISettings,
  CourseItem,
  CourseSettings,
  CoursesMap,
  PollIntervalSettings,
  PushdeerSettings,
} from '../types'
import { useAccounts } from '../hooks/useAccounts'
import AISection from '../components/settings/AISection'
import PushdeerSection from '../components/settings/PushdeerSection'
import MonitorSection from '../components/settings/MonitorSection'
import CoursesSection from '../components/settings/CoursesSection'

interface SettingsData {
  allCourses: CourseItem[]
  settings: CoursesMap
  ai: AISettings
  defaults: CourseSettings
  pushdeer: PushdeerSettings
  poll: PollIntervalSettings
}

export default function Settings() {
  const { t } = useTranslation()
  const { activeAccount } = useAccounts()
  const accountId = activeAccount?.id ?? null
  const base = accountId ? `/api/accounts/${accountId}` : null

  const [data, setData] = useState<SettingsData | null>(null)
  const [loading, setLoading] = useState(true)

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
      .then(([allCourses, settings, ai, defaults, pushdeer, poll]: [
        CourseItem[], CoursesMap, AISettings, CourseSettings, PushdeerSettings, PollIntervalSettings,
      ]) => {
        setData({ allCourses, settings, ai, defaults, pushdeer, poll })
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [base])

  const reloadAi = async () => {
    if (!base) return
    const ai: AISettings = await fetch(`${base}/ai/settings`).then((r) => r.json())
    setData((prev) => (prev ? { ...prev, ai } : prev))
  }
  const reloadPushdeer = async () => {
    if (!base) return
    const pushdeer: PushdeerSettings = await fetch(`${base}/pushdeer/settings`).then((r) => r.json())
    setData((prev) => (prev ? { ...prev, pushdeer } : prev))
  }

  if (loading || !base || !data) {
    return (
      <div className="page">
        <p className="empty-message">{t('common.loading')}</p>
      </div>
    )
  }

  return (
    <div className="page">
      <AISection base={base} ai={data.ai} onReload={reloadAi} />
      <PushdeerSection base={base} pushdeer={data.pushdeer} onReload={reloadPushdeer} />
      <MonitorSection base={base} initial={data.poll} />
      <CoursesSection
        base={base}
        allCourses={data.allCourses}
        settings={data.settings}
        defaults={data.defaults}
        pushdeerKeyCount={data.pushdeer.keys.length}
      />
    </div>
  )
}
