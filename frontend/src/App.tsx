import { type ReactNode } from 'react'
import { Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Settings from './pages/Settings'
import { AccountsProvider, useAccounts } from './hooks/useAccounts'
import AccountSwitcher from './components/AccountSwitcher'

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { loading, state, activeAccount } = useAccounts()
  if (loading) {
    return <div className="loading-screen">Loading...</div>
  }
  if (state.accounts.length === 0) {
    return <Navigate to="/login" replace />
  }
  if (!activeAccount || !activeAccount.logged_in) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

function AppInner() {
  const { t, i18n } = useTranslation()
  const { activeAccount, loading } = useAccounts()

  const toggleLanguage = () => {
    const next = i18n.language.startsWith('zh') ? 'en' : 'zh'
    i18n.changeLanguage(next)
  }

  const hasAuthedAccount = !!activeAccount?.logged_in

  return (
    <div className="app">
      <div className="sticky-header">
        <nav className="navbar">

          <div className="navbar-brand">{t('nav.brand')}</div>
          <div className="navbar-links">
            {hasAuthedAccount && (
              <>
                <NavLink
                  to="/dashboard"
                  className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                >
                  {t('nav.dashboard')}
                </NavLink>
                <NavLink
                  to="/settings"
                  className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                >
                  {t('nav.settings')}
                </NavLink>
              </>
            )}
          </div>
          <div className="navbar-actions">
            <AccountSwitcher />
            <button className="btn-ghost btn-sm" onClick={toggleLanguage}>
              {i18n.language.startsWith('zh') ? 'EN' : '中文'}
            </button>
          </div>
        </nav>
      </div>

      <main className="main-content">
        <Routes>
          <Route
            path="/"
            element={
              loading ? (
                <div className="loading-screen">Loading...</div>
              ) : hasAuthedAccount ? (
                <Navigate to="/dashboard" replace />
              ) : (
                <Navigate to="/login" replace />
              )
            }
          />
          <Route
            path="/login"
            element={
              loading ? (
                <div className="loading-screen">Loading...</div>
              ) : (
                <Login />
              )
            }
          />
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <Dashboard />
              </ProtectedRoute>
            }
          />
          <Route
            path="/settings"
            element={
              <ProtectedRoute>
                <Settings />
              </ProtectedRoute>
            }
          />
        </Routes>
      </main>

      <footer className="app-footer">
        <a
          className="app-footer-link"
          href="https://github.com/dvdsanyi/Yuketang-Helper-Web"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="GitHub"
        >
          <svg
            className="app-footer-icon"
            viewBox="0 0 24 24"
            width="16"
            height="16"
            aria-hidden="true"
          >
            <path
              fill="currentColor"
              d="M12 .5C5.73.5.67 5.56.67 11.83c0 5.02 3.25 9.27 7.77 10.77.57.1.78-.25.78-.55 0-.27-.01-.99-.02-1.94-3.16.69-3.83-1.52-3.83-1.52-.52-1.32-1.27-1.67-1.27-1.67-1.04-.71.08-.7.08-.7 1.15.08 1.75 1.18 1.75 1.18 1.02 1.74 2.67 1.24 3.32.95.1-.74.4-1.24.72-1.53-2.52-.29-5.18-1.26-5.18-5.6 0-1.24.44-2.25 1.17-3.04-.12-.29-.51-1.45.11-3.02 0 0 .96-.31 3.15 1.16a10.94 10.94 0 0 1 5.74 0c2.18-1.47 3.14-1.16 3.14-1.16.62 1.57.23 2.73.11 3.02.73.79 1.16 1.8 1.16 3.04 0 4.36-2.66 5.31-5.2 5.59.41.35.77 1.03.77 2.08 0 1.5-.01 2.71-.01 3.08 0 .3.21.66.79.55 4.51-1.5 7.76-5.75 7.76-10.77C23.33 5.56 18.27.5 12 .5Z"
            />
          </svg>
          <span>dvdsanyi/Yuketang-Helper-Web</span>
        </a>
        <span className="app-footer-version">{import.meta.env.VITE_APP_VERSION || 'dev'}</span>
      </footer>
    </div>
  )
}

export default function App() {
  return (
    <AccountsProvider>
      <AppInner />
    </AccountsProvider>
  )
}
