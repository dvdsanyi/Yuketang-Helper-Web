import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

type LoginStatus = 'waiting' | 'qr_ready' | 'scanning' | 'success' | 'error'

interface ServerOption {
  key: string
  label: string
  label_zh: string
}

interface LoginProps {
  onSuccess: () => void
}

export default function Login({ onSuccess }: LoginProps) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const wsRef = useRef<WebSocket | null>(null)
  const [status, setStatus] = useState<LoginStatus>('waiting')
  const [qrUrl, setQrUrl] = useState<string>('')
  const [errorMsg, setErrorMsg] = useState<string>('')
  const [domain, setDomain] = useState<string>('')
  const [serverOptions, setServerOptions] = useState<ServerOption[]>([])
  const domainRef = useRef<string>('')

  useEffect(() => {
    fetch('/api/domain')
      .then(r => r.json())
      .then(data => {
        setDomain(data.domain)
        domainRef.current = data.domain
        setServerOptions(data.options)
        saveDomainAndConnect(data.domain)
      })
    return () => { wsRef.current?.close() }
  }, [])

  function saveDomainAndConnect(d: string) {
    fetch('/api/domain', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain: d }),
    }).then(() => connectWs())
  }

  function handleDomainChange(newDomain: string) {
    setDomain(newDomain)
    domainRef.current = newDomain
    wsRef.current?.close()
    saveDomainAndConnect(newDomain)
  }

  function connectWs() {
    setStatus('waiting')
    setQrUrl('')
    setErrorMsg('')

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws/login`)
    wsRef.current = ws

    ws.onmessage = (ev) => {
      let msg: Record<string, unknown>
      try {
        msg = JSON.parse(ev.data as string) as Record<string, unknown>
      } catch {
        return
      }

      const type = msg['type'] as string

      if (type === 'qr') {
        setQrUrl(msg['url'] as string)
        setStatus('qr_ready')
      } else if (type === 'scanning') {
        setStatus('scanning')
      } else if (type === 'success') {
        setStatus('success')
        onSuccess()
        setTimeout(() => navigate('/dashboard'), 1000)
      } else if (type === 'error') {
        setErrorMsg((msg['message'] as string) || t('login.error'))
        setStatus('error')
      }
    }
  }

  const handleRefresh = () => {
    wsRef.current?.close()
    saveDomainAndConnect(domainRef.current)
  }

  return (
    <div className="login-container">
      <div className="login-card">
        <h1 className="login-title">{t('login.title')}</h1>
        <div className="form-group" style={{ marginBottom: '1rem', width: '100%' }}>
          <label className="form-label" style={{ marginBottom: '0.5rem', marginRight: '0.5rem' }}>{t('login.server')}</label>
          <select
            className="form-select"
            value={domain}
            onChange={e => handleDomainChange(e.target.value)}
            disabled={status === 'waiting' || status === 'scanning' || status === 'success'}
          >
            {serverOptions.map(opt => (
              <option key={opt.key} value={opt.key}>
                {i18n.language.startsWith('zh') ? opt.label_zh : opt.label}
              </option>
            ))}
          </select>
        </div>

        <div className="qr-wrapper">
          {status === 'waiting' && (
            <div className="qr-placeholder">
              <div className="spinner" />
              <span>{t('login.waiting')}</span>
            </div>
          )}

          {(status === 'qr_ready' || status === 'scanning') && qrUrl && (
            <>
              <img
                src={qrUrl}
                alt="QR Code"
                className={`qr-image ${status === 'scanning' ? 'qr-dimmed' : ''}`}
              />
              {status === 'scanning' && (
                <div className="qr-overlay">
                  <div className="spinner spinner-white" />
                  <span>{t('login.scanning')}</span>
                </div>
              )}
            </>
          )}

          {status === 'success' && (
            <div className="qr-placeholder qr-success">
              <div className="success-icon">✓</div>
              <span>{t('login.success')}</span>
            </div>
          )}

          {status === 'error' && (
            <div className="qr-placeholder qr-error">
              <div className="error-icon">✕</div>
              <span>{errorMsg || t('login.error')}</span>
            </div>
          )}
        </div>

        {(status === 'qr_ready' || status === 'error') && (
          <button className="btn btn-secondary" onClick={handleRefresh}>
            {t('login.refresh')}
          </button>
        )}

        <p className="login-note">{t('login.note')}</p>
      </div>
    </div>
  )
}
