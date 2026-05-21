export interface NotificationSub {
  enabled: boolean
  signin: boolean
  problem: boolean
  call: boolean
  danmu: boolean
  red_packet: boolean
}

export interface CourseItem {
  classroom_id: string
  name: string
  teacher_name: string | null
  active: boolean
}

export interface PushdeerKeyEntry {
  name: string
  endpoint: string
  push_key: string
}

export interface PushdeerSettings {
  keys: PushdeerKeyEntry[]
  active_key: number
  language: 'zh' | 'en'
}

export interface AccountSummary {
  id: string
  name: string
  avatar: string
  domain: string
  logged_in: boolean
}

export interface AccountsState {
  active_account_id: string | null
  accounts: AccountSummary[]
}

export interface DomainOption {
  key: string
  label: string
  label_zh: string
}
