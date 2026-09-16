import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { PollIntervalSettings } from '../../types'

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

export default function MonitorSection({
  base,
  initial,
}: {
  base: string
  initial: PollIntervalSettings
}) {
  const { t } = useTranslation()
  const [settings, setSettings] = useState<PollIntervalSettings>(initial)
  const [input, setInput] = useState<string>(String(initial.poll_interval))
  const [status, setStatus] = useState<SaveStatus>('idle')

  const handleSave = async () => {
    const parsed = parseInt(input, 10)
    if (!Number.isFinite(parsed)) {
      setStatus('error')
      return
    }
    setStatus('saving')
    try {
      const resp = await fetch(`${base}/poll-interval`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ poll_interval: parsed }),
      })
      if (!resp.ok) throw new Error('Save failed')
      const data: { ok: boolean; poll_interval: number } = await resp.json()
      setSettings((prev) => ({ ...prev, poll_interval: data.poll_interval }))
      setInput(String(data.poll_interval))
      setStatus('saved')
      setTimeout(() => setStatus('idle'), 2000)
    } catch {
      setStatus('error')
    }
  }

  const buttonClass =
    status === 'saved' ? 'btn-success'
    : status === 'error' ? 'btn-danger'
    : 'btn-primary'

  return (
    <section className="settings-section">
      <h2 className="settings-section-title">{t('settings.monitorSettings')}</h2>
      <div className="card">
        <div className="form-row form-row-padded form-row-padded-y">
          <label className="form-label">
            {t('settings.pollInterval')}
            <span
              className="tooltip-trigger"
              data-tooltip={t('settings.pollIntervalDesc', {
                min: settings.min,
                max: settings.max,
                default: settings.default,
              })}
            >
              ?
            </span>
          </label>
          <div className="input-with-unit input-with-unit-row">
            <input
              type="number"
              className="form-input-number"
              min={settings.min}
              max={settings.max}
              value={input}
              onChange={(e) => {
                setInput(e.target.value)
                setStatus('idle')
              }}
            />
            <span className="input-unit">{t('settings.seconds')}</span>
            <button
              className={`btn btn-sm ${buttonClass}`}
              onClick={handleSave}
              disabled={status === 'saving' || input === String(settings.poll_interval)}
            >
              {status === 'saving'
                ? t('settings.applying')
                : status === 'saved'
                  ? t('settings.applied')
                  : t('settings.apply')}
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}
