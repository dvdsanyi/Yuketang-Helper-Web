import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Language, PushdeerKeyEntry, PushdeerSettings } from '../../types'
import MiddleTruncate from '../MiddleTruncate'

type TestState = { status: 'idle' | 'testing' | 'success' | 'error'; message: string }
const IDLE: TestState = { status: 'idle', message: '' }

export default function PushdeerSection({
  base,
  pushdeer,
  onReload,
}: {
  base: string
  pushdeer: PushdeerSettings
  onReload: () => Promise<void>
}) {
  const { t } = useTranslation()
  const [newKey, setNewKey] = useState<PushdeerKeyEntry>({
    name: '',
    endpoint: 'https://api2.pushdeer.com',
    push_key: '',
  })
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState(false)
  const [tests, setTests] = useState<Record<number, TestState>>({})

  const setTest = (index: number, next: TestState) =>
    setTests((prev) => ({ ...prev, [index]: next }))

  const handleAdd = async () => {
    if (!newKey.name.trim() || !newKey.push_key.trim() || !newKey.endpoint.trim()) return
    setAdding(true)
    setAddError(false)
    try {
      const resp = await fetch(`${base}/pushdeer/keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newKey.name.trim(),
          endpoint: newKey.endpoint.trim(),
          push_key: newKey.push_key.trim(),
        }),
      })
      if (!resp.ok) throw new Error('Add failed')
      setNewKey({ name: '', endpoint: 'https://api2.pushdeer.com', push_key: '' })
      await onReload()
    } catch {
      setAddError(true)
      setTimeout(() => setAddError(false), 4000)
    }
    setAdding(false)
  }

  const handleDelete = async (index: number) => {
    await fetch(`${base}/pushdeer/keys/${index}`, { method: 'DELETE' })
    await onReload()
  }

  const handleSetActive = async (index: number) => {
    await fetch(`${base}/pushdeer/active`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_key: index }),
    })
    await onReload()
  }

  const handleSetLanguage = async (language: Language) => {
    await fetch(`${base}/pushdeer/language`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ language }),
    })
    await onReload()
  }

  const handleTest = async (index: number) => {
    setTest(index, { status: 'testing', message: '' })
    try {
      const resp = await fetch(`${base}/pushdeer/test/${index}`, { method: 'POST' })
      const data: { ok: boolean; message: string } = await resp.json()
      setTest(index, {
        status: data.ok ? 'success' : 'error',
        message: data.ok ? '' : (data.message || ''),
      })
    } catch (e) {
      setTest(index, { status: 'error', message: String(e) })
    } finally {
      setTimeout(() => setTest(index, IDLE), 4000)
    }
  }

  return (
    <section className="settings-section">
      <h2 className="settings-section-title">{t('settings.pushdeerSettings')}</h2>
      <div className="card">
        {pushdeer.keys.length > 0 && (
          <div className="credential-list">
            {pushdeer.keys.map((entry, idx) => {
              const test = tests[idx] ?? IDLE
              return (
                <div key={idx} className={`credential-item ${idx === pushdeer.active_key ? 'credential-active' : ''}`}>
                  <div className="credential-info">
                    <MiddleTruncate className="credential-name" text={entry.name} />
                    <span className="credential-meta">{entry.endpoint}</span>
                    <span className="credential-masked">{entry.push_key}</span>
                  </div>
                  <div className="credential-actions">
                    <button
                      className={`btn btn-sm ${idx === pushdeer.active_key ? 'btn-success' : 'btn-secondary'}`}
                      onClick={() => handleSetActive(idx)}
                    >
                      {idx === pushdeer.active_key ? t('settings.inUse') : t('settings.use')}
                    </button>
                    <button
                      className={`btn btn-sm ${test.status === 'success' ? 'btn-success' : test.status === 'error' ? 'btn-danger' : 'btn-secondary'}`}
                      onClick={() => handleTest(idx)}
                      disabled={test.status === 'testing'}
                      title={test.message || undefined}
                    >
                      {test.status === 'testing'
                        ? t('settings.pushdeerTesting')
                        : test.status === 'success'
                          ? t('settings.pushdeerTestSuccess')
                          : test.status === 'error'
                            ? t('settings.pushdeerTestFailed')
                            : t('settings.pushdeerTest')}
                    </button>
                    <button className="btn btn-sm btn-danger" onClick={() => handleDelete(idx)}>
                      {t('common.delete')}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {pushdeer.keys.length > 0 && (
          <div className="form-row form-row-padded">
            <label className="form-label">{t('settings.pushdeerLanguage')}</label>
            <div className="toggle-group">
              <button
                className={`toggle-option ${pushdeer.language === 'zh' ? 'selected' : ''}`}
                onClick={() => handleSetLanguage('zh')}
              >
                中文
              </button>
              <button
                className={`toggle-option ${pushdeer.language === 'en' ? 'selected' : ''}`}
                onClick={() => handleSetLanguage('en')}
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
              value={newKey.name}
              placeholder={t('settings.keyNamePlaceholder')}
              onChange={(e) => setNewKey((prev) => ({ ...prev, name: e.target.value }))}
            />
            <input
              type="text"
              className="form-input"
              value={newKey.endpoint}
              placeholder={t('settings.pushdeerEndpointPlaceholder')}
              onChange={(e) => setNewKey((prev) => ({ ...prev, endpoint: e.target.value }))}
            />
            <input
              type="password"
              className="form-input"
              value={newKey.push_key}
              placeholder={t('settings.apiKeyPlaceholder')}
              onChange={(e) => setNewKey((prev) => ({ ...prev, push_key: e.target.value }))}
            />
          </div>
          <button
            className={`btn ${addError ? 'btn-danger' : 'btn-primary'}`}
            onClick={handleAdd}
            disabled={adding || !newKey.name.trim() || !newKey.push_key.trim() || !newKey.endpoint.trim()}
          >
            {adding
              ? t('settings.applying')
              : addError
                ? t('settings.addKeyFailed')
                : t('settings.addKey')}
          </button>
        </div>
        <p className="settings-card-hint">{t('settings.pushdeerEndpointHint')}</p>
      </div>
    </section>
  )
}
