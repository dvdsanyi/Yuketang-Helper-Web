import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { DomainOption } from '../types'
import { useAccounts } from '../hooks/useAccounts'

type LoginStatus = 'idle' | 'waiting' | 'qr_ready' | 'success'
type LoginMethod = 'qrcode' | 'password'

const CAPTCHA_SRC = 'https://turing.captcha.qcloud.com/TCaptcha.js'

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

  const cleanupPending = useCallback(() => {
    if (!pendingIdRef.current) return
    fetch(`/api/accounts/${pendingIdRef.current}`, { method: 'DELETE' }).catch(() => {})
    pendingIdRef.current = null
  }, [])

  const createPendingAccount = useCallback(async (d: string): Promise<string> => {
    cleanupPending()
    const resp = await fetch('/api/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain: d }),
    })
    const data = await resp.json()
    pendingIdRef.current = data.account_id as string
    return pendingIdRef.current
  }, [cleanupPending])

  // Mark login as complete and hand off to the Dashboard route. The pause
  // gives the user a moment to see the confirmation before the redirect.
  const completeLogin = useCallback(async () => {
    setStatus('success')
    pendingIdRef.current = null
    await refresh()
    setTimeout(() => navigate('/dashboard'), 800)
  }, [navigate, refresh])

  const startQrFlow = useCallback(async (d: string) => {
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
        void completeLogin()
      }
    }
  }, [createPendingAccount, completeLogin])

  // Load domain options on mount + clean up pending account on unmount
  useEffect(() => {
    fetch('/api/domains')
      .then((r) => r.json())
      .then((data: { options: DomainOption[]; default: string }) => {
        setServerOptions(data.options)
        setDomain(data.default)
      })
    return () => {
      wsRef.current?.close()
      cleanupPending()
    }
  }, [cleanupPending])

  // Start QR flow when domain/method becomes ready. `status` is intentionally
  // excluded from the deps: startQrFlow itself drives status
  // (idle→waiting→qr_ready), so depending on it would re-fire this effect on
  // every transition and restart the whole flow (new pending account + socket)
  // in a loop. method/domain/startQrFlow are the only real triggers.
  useEffect(() => {
    if (method !== 'qrcode' || !domain || status === 'success') return
    void startQrFlow(domain)
    return () => {
      wsRef.current?.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [method, domain, startQrFlow])

  const handleDomainChange = (newDomain: string) => {
    setDomain(newDomain)
    wsRef.current?.close()
  }

  const handleMethodChange = (m: LoginMethod) => {
    setMethod(m)
    setPwError('')
    // Just close the socket; switching to qrcode re-runs the effect below
    // (keyed on `method`), which restarts the QR flow exactly once. Calling
    // startQrFlow here too would double-create the pending account + socket.
    wsRef.current?.close()
  }

  const handleRefresh = () => {
    wsRef.current?.close()
    if (domain) void startQrFlow(domain)
  }

  // Tencent's widget used to be a blocking <script> in index.html, which left
  // the whole app blank whenever that host was slow or unreachable. Load it
  // here instead: only password login needs it, and a failure now costs one
  // login method rather than the entire page.
  const loadCaptcha = (): Promise<void> => {
    if (window.TencentCaptcha) return Promise.resolve()
    return new Promise((resolve, reject) => {
      const existing = document.querySelector<HTMLScriptElement>(`script[src="${CAPTCHA_SRC}"]`)
      const script = existing ?? document.createElement('script')
      script.addEventListener('load', () => resolve())
      script.addEventListener('error', () => reject(new Error('captcha unavailable')))
      if (!existing) {
        script.src = CAPTCHA_SRC
        script.async = true
        document.head.appendChild(script)
      }
    })
  }

  const showCaptcha = async (): Promise<{ ticket: string; randstr: string } | null> => {
    await loadCaptcha()
    return new Promise((resolve) => {
      const captcha = new window.TencentCaptcha('2091064951', (res) => {
        // Tencent's widget caches itself in the DOM; destroy() removes the
        // overlay so repeated dismissals don't leak nodes/listeners.
        captcha.destroy()
        resolve(res.ret === 0 ? { ticket: res.ticket, randstr: res.randstr } : null)
      })
      captcha.show()
    })
  }

  const handlePasswordLogin = async () => {
    if (!phone || !password) {
      setPwError(t('login.pwFillAll'))
      return
    }
    setPwError('')
    setPwLoading(true)
    try {
      const captchaResult = await showCaptcha()
      if (!captchaResult) return  // user dismissed the captcha
      const aid = await createPendingAccount(domain)
      const resp = await fetch(`/api/accounts/${aid}/auth/password-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, password, ...captchaResult }),
      })
      const data = await resp.json()
      if (!data.ok) {
        setPwError(data.message || t('login.pwFailed'))
        return
      }
      await completeLogin()
    } catch (err) {
      setPwError(String(err))
    } finally {
      setPwLoading(false)
    }
  }

  const handleCancel = () => {
    wsRef.current?.close()
    cleanupPending()
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

        <div className="login-form-row">
          <label className="form-label login-form-label">{t('login.server')}</label>
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
                  <span>{t('login.success')}</span>
                </div>
              </div>
            ) : (
              <div className="login-form-block">
                <div className="login-form-field">
                  <input
                    type="tel"
                    className="form-input login-form-input"
                    placeholder={t('login.phonePlaceholder')}
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    disabled={pwLoading}
                  />
                </div>
                <div className="login-form-field">
                  <input
                    type="password"
                    className="form-input login-form-input"
                    placeholder={t('login.passwordPlaceholder')}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={pwLoading}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handlePasswordLogin()
                    }}
                  />
                </div>
                {pwError && <p className="login-form-error">{pwError}</p>}
                <button
                  className="btn btn-primary login-submit"
                  onClick={handlePasswordLogin}
                  disabled={pwLoading}
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
