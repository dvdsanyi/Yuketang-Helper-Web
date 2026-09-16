import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AIKeyEntry, AISettings, AIProviderName } from '../../types'

const PROVIDER_LABELS: Record<string, string> = {
  google: 'Google',
  qwen: 'ModelScope',
}

export default function AISection({
  base,
  ai,
  onReload,
}: {
  base: string
  ai: AISettings
  onReload: () => Promise<void>
}) {
  const { t } = useTranslation()
  const [newKey, setNewKey] = useState<AIKeyEntry>({ name: '', provider: 'qwen', key: '' })
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState(false)

  const handleAdd = async () => {
    if (!newKey.name.trim() || !newKey.key.trim()) return
    setAdding(true)
    setAddError(false)
    try {
      const resp = await fetch(`${base}/ai/keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newKey),
      })
      if (!resp.ok) throw new Error('Add failed')
      setNewKey({ name: '', provider: 'qwen', key: '' })
      await onReload()
    } catch {
      setAddError(true)
      setTimeout(() => setAddError(false), 4000)
    }
    setAdding(false)
  }

  const handleDelete = async (index: number) => {
    await fetch(`${base}/ai/keys/${index}`, { method: 'DELETE' })
    await onReload()
  }

  const handleSetActive = async (index: number) => {
    await fetch(`${base}/ai/active`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_key: index }),
    })
    await onReload()
  }

  const handleToggleFallback = async (enabled: boolean) => {
    await fetch(`${base}/ai/fallback`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fallback_keys: enabled }),
    })
    await onReload()
  }

  return (
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
                    onClick={() => handleSetActive(idx)}
                  >
                    {idx === ai.active_key ? t('settings.inUse') : t('settings.use')}
                  </button>
                  <button className="btn btn-sm btn-danger" onClick={() => handleDelete(idx)}>
                    {t('common.delete')}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {ai.keys.length > 1 && (
          <div className="form-row form-row-padded">
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
              onChange={(e) => setNewKey((prev) => ({ ...prev, name: e.target.value }))}
            />
            <select
              className="form-select"
              value={newKey.provider}
              onChange={(e) => setNewKey((prev) => ({ ...prev, provider: e.target.value as AIProviderName }))}
            >
              <option value="google">Google</option>
              <option value="qwen">ModelScope</option>
            </select>
            <input
              type="password"
              className="form-input"
              value={newKey.key}
              placeholder={t('settings.apiKeyPlaceholder')}
              onChange={(e) => setNewKey((prev) => ({ ...prev, key: e.target.value }))}
            />
          </div>
          <button
            className={`btn ${addError ? 'btn-danger' : 'btn-primary'}`}
            onClick={handleAdd}
            disabled={adding || !newKey.name.trim() || !newKey.key.trim()}
          >
            {adding
              ? t('settings.applying')
              : addError
                ? t('settings.addKeyFailed')
                : t('settings.addKey')}
          </button>
        </div>
        <p className="settings-card-hint">{t('common.betaWarning')}</p>
      </div>
    </section>
  )
}
