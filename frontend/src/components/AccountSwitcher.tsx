import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAccounts } from '../hooks/useAccounts'

export default function AccountSwitcher() {
  const { t } = useTranslation()
  const { state, activeAccount, setActive, deleteAccount } = useAccounts()
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  const onSwitch = async (id: string) => {
    const target = state.accounts.find((a) => a.id === id)
    await setActive(id)
    setOpen(false)
    // If the target account's session has expired, route to /login so the
    // user can re-authenticate that account instead of landing on an
    // empty Dashboard that ProtectedRoute would bounce anyway.
    if (target && !target.logged_in) {
      navigate('/login')
    }
  }

  const onAdd = () => {
    setOpen(false)
    navigate('/login')
  }

  const onDelete = async (id: string) => {
    if (!confirm(t('accounts.confirmDelete'))) return
    await deleteAccount(id)
  }

  const label = activeAccount
    ? (activeAccount.name || activeAccount.id)
    : t('accounts.noAccount')

  return (
    <div className="account-switcher" ref={ref}>
      <button className="account-switcher-trigger btn-ghost btn-sm" onClick={() => setOpen((v) => !v)}>
        <span className="account-name">{label}</span>
        <span className="account-caret">▾</span>
      </button>
      {open && (
        <div className="account-dropdown">
          <div className="account-dropdown-header">{t('accounts.title')}</div>
          {state.accounts.length === 0 && (
            <div className="account-dropdown-empty">{t('accounts.empty')}</div>
          )}
          {state.accounts.map((a) => {
            const isActive = a.id === state.active_account_id
            return (
              <div key={a.id} className={`account-row ${isActive ? 'account-row-active' : ''}`}>
                <div className="account-row-info">
                  <span className="account-row-name">{a.name || a.id}</span>
                  <span className="account-row-sub">{a.domain}</span>
                </div>
                <div className="account-row-actions">
                  <button
                    className={`btn btn-sm ${isActive ? 'btn-success' : 'btn-secondary'}`}
                    onClick={() => onSwitch(a.id)}
                    disabled={isActive}
                  >
                    {isActive ? t('accounts.viewing') : t('accounts.switch')}
                  </button>
                  <button className="btn btn-sm btn-danger" onClick={() => onDelete(a.id)}>
                    {t('common.delete')}
                  </button>
                </div>
              </div>
            )
          })}
          <button className="account-add" onClick={onAdd}>+ {t('accounts.add')}</button>
        </div>
      )}
    </div>
  )
}
