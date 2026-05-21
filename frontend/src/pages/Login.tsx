import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { DomainOption } from '../types'
import { useAccounts } from '../hooks/useAccounts'

type LoginStatus = 'idle' | 'waiting' | 'qr_ready' | 'success'
type LoginMethod = 'qrcode' | 'password'

declare global {
  interface Window {
    TencentCaptcha: new (
      appId: string,
      callback: (res: { ret: number; ticket: string; randstr: string }) => void,
    ) => { show: () => void; destroy: () => void }
  }
}

export default function Login() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { refresh, state } = useAccounts()
  const canCancel = state.accounts.some((a) => a.logged_in)
  const wsRef = useRef<WebSocket | null>(null)
  const pendingIdRef = useRef<string | null>(null)
  const [status, setStatus] = useState<LoginStatus>('idle')
  const [qrUrl, setQrUrl] = useState<string>('')
  const [domain, setDomain] = useState<string>('')
  const [serverOptions, setServerOptions] = useState<DomainOption[]>([])

  const [method, setMethod] = useState<LoginMethod>('qrcode')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [pwLoading, setPwLoading] = useState(false)
  const [pwError, setPwError] = useState('')

  // Load domain options on mount
  useEffect(() => {
    fetch('/api/domains')
      .then((r) => r.json())
      .then((data: { options: DomainOption[]; default: string }) => {
        setServerOptions(data.options)
        setDomain(data.default)
      })
    return () => {
      wsRef.current?.close()
      // Clean up pending account if user leaves without completing login
      if (pendingIdRef.current) {
        fetch(`/api/accounts/${pendingIdRef.current}`, { method: 'DELETE' }).catch(() => {})
        pendingIdRef.current = null
      }
    }
  }, [])

  // Start QR flow when domain/method becomes ready
  useEffect(() => {
    if (method !== 'qrcode' || !domain || status === 'success') return
    void startQrFlow(domain)
    return () => {
      wsRef.current?.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [method, domain])

  async function createPendingAccount(d: string): Promise<string> {
    // Reuse existing pending id if domain unchanged
    if (pendingIdRef.current) {
      await fetch(`/api/accounts/${pendingIdRef.current}`, { method: 'DELETE' }).catch(() => {})
      pendingIdRef.current = null
    }
    const resp = await fetch('/api/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain: d }),
    })
    const data = await resp.json()
    pendingIdRef.current = data.account_id as string
    return pendingIdRef.current
  }

  async function startQrFlow(d: string) {
    setStatus('waiting')
    setQrUrl('')
    const aid = await createPendingAccount(d)

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws/accounts/${aid}/login`)
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
      } else if (type === 'success') {
        setStatus('success')
        pendingIdRef.current = null
        void refresh().then(() => setTimeout(() => navigate('/dashboard'), 800))
      }
    }
  }

  async function handleDomainChange(newDomain: string) {
    setDomain(newDomain)
    wsRef.current?.close()
  }

  async function handleMethodChange(m: LoginMethod) {
    setMethod(m)
    setPwError('')
    wsRef.current?.close()
    if (m === 'qrcode' && domain) {
      void startQrFlow(domain)
    }
  }

  const handleRefresh = () => {
    wsRef.current?.close()
    if (domain) void startQrFlow(domain)
  }

  async function handlePasswordLogin() {
    if (!phone || !password) {
      setPwError(t('login.pwFillAll'))
      return
    }
    setPwError('')
    setPwLoading(true)

    const aid = await createPendingAccount(domain)

    const captcha = new window.TencentCaptcha('2091064951', (res) => {
      if (res.ret !== 0) {
        setPwLoading(false)
        return
      }

      fetch(`/api/accounts/${aid}/auth/password-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone,
          password,
          ticket: res.ticket,
          randstr: res.randstr,
        }),
      })
        .then((r) => r.json())
        .then((data) => {
          setPwLoading(false)
          if (data.ok) {
            setStatus('success')
            pendingIdRef.current = null
            void refresh().then(() => setTimeout(() => navigate('/dashboard'), 800))
          } else {
            setPwError(data.error || t('login.pwFailed'))
          }
        })
        .catch((err) => {
          setPwLoading(false)
          setPwError(String(err))
        })
    })
    captcha.show()
  }

  const handleCancel = () => {
    wsRef.current?.close()
    if (pendingIdRef.current) {
      fetch(`/api/accounts/${pendingIdRef.current}`, { method: 'DELETE' }).catch(() => {})
      pendingIdRef.current = null
    }
    navigate('/dashboard')
  }

  return (
    <div className="login-container">
      <div className="login-card">
        {canCancel && (
          <button className="btn btn-ghost btn-sm login-cancel" onClick={handleCancel}>
            ← {t('login.cancel')}
          </button>
        )}
        <div className="login-method-toggle">
          <button
            className={`method-tab ${method === 'qrcode' ? 'active' : ''}`}
            onClick={() => handleMethodChange('qrcode')}
            disabled={status === 'success'}
          >
            {t('login.methodQR')}
          </button>
          <button
            className={`method-tab ${method === 'password' ? 'active' : ''}`}
            onClick={() => handleMethodChange('password')}
            disabled={status === 'success'}
          >
            {t('login.methodPassword')}
          </button>
        </div>

        <div className="form-group" style={{ marginBottom: '1rem', width: '100%' }}>
          <label className="form-label" style={{ marginBottom: '0.5rem', marginRight: '0.5rem' }}>
            {t('login.server')}
          </label>
          <select
            className="form-select"
            value={domain}
            onChange={(e) => handleDomainChange(e.target.value)}
            disabled={status === 'waiting' || status === 'success'}
          >
            {serverOptions.map((opt) => (
              <option key={opt.key} value={opt.key}>
                {i18n.language.startsWith('zh') ? opt.label_zh : opt.label}
              </option>
            ))}
          </select>
        </div>

        {method === 'qrcode' ? (
          <>
            <div className="qr-wrapper">
              {(status === 'idle' || status === 'waiting') && (
                <div className="qr-placeholder">
                  <div className="spinner" />
                  <span>{t('login.waiting')}</span>
                </div>
              )}

              {status === 'qr_ready' && qrUrl && (
                <img src={qrUrl} alt="QR Code" className="qr-image" />
              )}

              {status === 'success' && (
                <div className="qr-placeholder qr-success">
                  <div className="success-icon">✓</div>
                  <span>{t('login.success')}</span>
                </div>
              )}
            </div>

            {status === 'qr_ready' && (
              <button className="btn btn-secondary" onClick={handleRefresh}>
                {t('login.refresh')}
              </button>
            )}
          </>
        ) : (
          <>
            {status === 'success' ? (
              <div className="qr-wrapper">
                <div className="qr-placeholder qr-success">
                  <div className="success-icon">✓</div>
                  <span>{t('login.success')}</span>
                </div>
              </div>
            ) : (
              <div style={{ width: '100%' }}>
                <div className="form-group" style={{ marginBottom: '0.75rem' }}>
                  <input
                    type="tel"
                    className="form-input"
                    placeholder={t('login.phonePlaceholder')}
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    disabled={pwLoading}
                    style={{ width: '100%', padding: '0.5rem 0.75rem' }}
                  />
                </div>
                <div className="form-group" style={{ marginBottom: '0.75rem' }}>
                  <input
                    type="password"
                    className="form-input"
                    placeholder={t('login.passwordPlaceholder')}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={pwLoading}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handlePasswordLogin()
                    }}
                    style={{ width: '100%', padding: '0.5rem 0.75rem' }}
                  />
                </div>
                {pwError && (
                  <p style={{ color: 'var(--color-error, #e53e3e)', fontSize: '0.85rem', marginBottom: '0.75rem' }}>
                    {pwError}
                  </p>
                )}
                <button
                  className="btn btn-primary"
                  onClick={handlePasswordLogin}
                  disabled={pwLoading}
                  style={{ width: '100%' }}
                >
                  {pwLoading ? t('login.pwLoggingIn') : t('login.pwLogin')}
                </button>
              </div>
            )}
          </>
        )}

        <p className="login-note">{t('login.note')}</p>
      </div>
    </div>
  )
}
