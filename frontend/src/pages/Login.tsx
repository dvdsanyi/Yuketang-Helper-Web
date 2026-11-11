import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { DomainOption } from '../types'
import { useAccounts } from '../hooks/useAccounts'

type LoginStatus = 'idle' | 'waiting' | 'qr_ready' | 'error' | 'success'
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

// Allocate a login slot on the backend; it remembers the chosen server until
// the login completes.
async function createPendingAccount(domain: string): Promise<string> {
  const resp = await fetch('/api/accounts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ domain }),
  })
  const data: { account_id: string } = await resp.json()
  return data.account_id
}

function deletePendingAccount(id: string): void {
  fetch(`/api/accounts/${id}`, { method: 'DELETE' }).catch(() => {})
}

// Tencent's widget is loaded on demand rather than from index.html: only
// password login needs it, and that host being slow or unreachable then costs
// one login method instead of blanking the whole page.
function loadCaptcha(): Promise<void> {
  if (window.TencentCaptcha) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = CAPTCHA_SRC
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => {
      script.remove()  // so the next attempt loads it afresh
      reject(new Error('captcha unavailable'))
    }
    document.head.appendChild(script)
  })
}

export default function Login() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { refresh, activeAccount } = useAccounts()
  const wsRef = useRef<WebSocket | null>(null)
  const pendingIdRef = useRef<string | null>(null)
  // Bumped whenever the QR flow restarts or is torn down, so a flow still
  // waiting on its pending account can tell it has been superseded.
  const flowRef = useRef(0)
  const [status, setStatus] = useState<LoginStatus>('idle')
  const [qrUrl, setQrUrl] = useState<string>('')
  const [qrError, setQrError] = useState('')
  const [domain, setDomain] = useState<string>('')
  const [serverOptions, setServerOptions] = useState<DomainOption[]>([])

  const [method, setMethod] = useState<LoginMethod>('qrcode')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [pwLoading, setPwLoading] = useState(false)
  const [pwError, setPwError] = useState('')

  // Track `next` as the login slot in use, releasing the one it replaces.
  const setPending = useCallback((next: string | null) => {
    if (pendingIdRef.current) deletePendingAccount(pendingIdRef.current)
    pendingIdRef.current = next
  }, [])

  // Mark login as complete and hand off to the Dashboard route. The pause
  // gives the user a moment to see the confirmation before the redirect.
  const completeLogin = useCallback(async () => {
    setStatus('success')
    pendingIdRef.current = null
    await refresh()
    setTimeout(() => navigate('/dashboard'), 800)
  }, [navigate, refresh])

  const startQrFlow = useCallback(async (d: string) => {
    const flow = ++flowRef.current
    wsRef.current?.close()
    setStatus('waiting')
    setQrUrl('')
    setQrError('')
    let aid: string
    try {
      aid = await createPendingAccount(d)
    } catch {
      if (flow === flowRef.current) setStatus('error')
      return
    }
    if (flow !== flowRef.current) {
      deletePendingAccount(aid)
      return
    }
    setPending(aid)

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws/accounts/${aid}/login`)
    wsRef.current = ws

    ws.onmessage = (ev) => {
      if (flow !== flowRef.current) return
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
      } else if (type === 'error') {
        setQrError(String(msg['message'] ?? ''))
        setStatus('error')
      }
    }
    // The backend only hangs up once the login has succeeded or failed, so a
    // socket lost before that leaves a QR code nothing is listening for.
    ws.onclose = () => {
      if (flow !== flowRef.current) return
      setStatus((s) => (s === 'waiting' || s === 'qr_ready' ? 'error' : s))
    }
  }, [setPending, completeLogin])

  // Load domain options on mount + release the pending account on unmount.
  // Signing back in to an expired account starts on that account's server.
  useEffect(() => {
    const expiredOn = activeAccount && !activeAccount.logged_in ? activeAccount.domain : null
    fetch('/api/domains')
      .then((r) => r.json())
      .then((data: { options: DomainOption[]; default: string }) => {
        setServerOptions(data.options)
        setDomain(expiredOn ?? data.default)
      })
    return () => setPending(null)
  }, [setPending])

  // Start QR flow when domain/method becomes ready, and stop it when either
  // changes or the page is left. `status` is intentionally excluded from the
  // deps: startQrFlow itself drives status (idle→waiting→qr_ready/error), so
  // depending on it would re-fire this effect on every transition and restart
  // the whole flow (new pending account + socket) in a loop.
  useEffect(() => {
    if (method !== 'qrcode' || !domain || status === 'success') return
    void startQrFlow(domain)
    return () => {
      flowRef.current++
      wsRef.current?.close()
    }
  }, [method, domain, startQrFlow])

  const handleMethodChange = (m: LoginMethod) => {
    setMethod(m)
    setPwError('')
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
      setPending(aid)
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

  return (
    <div className="login-container">
      <div className="login-card">
        {activeAccount?.logged_in && (
          <button className="btn btn-ghost btn-sm login-cancel" onClick={() => navigate('/dashboard')}>
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
            onChange={(e) => setDomain(e.target.value)}
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

              {status === 'error' && (
                <div className="qr-placeholder qr-error">
                  <span>{t('login.qrFailed')}</span>
                </div>
              )}

              {status === 'success' && (
                <div className="qr-placeholder qr-success">
                  <span>{t('login.success')}</span>
                </div>
              )}
            </div>

            {status === 'error' && qrError && (
              <p
                className="login-form-error qr-error-detail"
                onMouseEnter={(e) => {
                  const el = e.currentTarget
                  el.title = el.scrollHeight > el.clientHeight ? qrError : ''
                }}
              >
                {qrError}
              </p>
            )}

            {(status === 'qr_ready' || status === 'error') && (
              <button className="btn btn-secondary" onClick={() => void startQrFlow(domain)}>
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
