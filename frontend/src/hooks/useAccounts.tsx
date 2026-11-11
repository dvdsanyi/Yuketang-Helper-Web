import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import type { AccountsState, AccountSummary } from '../types'

interface Ctx {
  state: AccountsState
  loading: boolean
  activeAccount: AccountSummary | null
  refresh: () => Promise<void>
  setActive: (id: string) => Promise<void>
  deleteAccount: (id: string) => Promise<void>
}

const AccountsContext = createContext<Ctx | null>(null)

async function fetchAccounts(): Promise<AccountsState> {
  const r = await fetch('/api/accounts')
  return (await r.json()) as AccountsState
}

export function AccountsProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AccountsState>({ active_account_id: null, accounts: [] })
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const s = await fetchAccounts()
      setState(s)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh().catch(() => setLoading(false))
  }, [refresh])

  const setActive = useCallback(async (id: string) => {
    await fetch('/api/accounts/active', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: id }),
    })
    await refresh()
  }, [refresh])

  const deleteAccount = useCallback(async (id: string) => {
    await fetch(`/api/accounts/${id}`, { method: 'DELETE' })
    await refresh()
  }, [refresh])

  const activeAccount =
    state.accounts.find((a) => a.id === state.active_account_id) ?? null

  return (
    <AccountsContext.Provider value={{ state, loading, activeAccount, refresh, setActive, deleteAccount }}>
      {children}
    </AccountsContext.Provider>
  )
}

export function useAccounts(): Ctx {
  const ctx = useContext(AccountsContext)
  if (!ctx) throw new Error('useAccounts must be used inside AccountsProvider')
  return ctx
}
