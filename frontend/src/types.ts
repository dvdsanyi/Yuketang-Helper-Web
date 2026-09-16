export type Language = 'zh' | 'en'

export type ChoiceAnswerMode = 'ai' | 'random' | 'off'
export type ShortAnswerMode = 'ai' | 'blank' | 'off'
export type AIProviderName = 'google' | 'qwen'

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
  classroom_name: string
  teacher_name: string | null
  active: boolean
}

export interface CourseSettings {
  type1: ChoiceAnswerMode
  type2: ChoiceAnswerMode
  type3: ChoiceAnswerMode
  type4: 'off'
  type5: ShortAnswerMode
  course_enabled: boolean
  answer_last5s: boolean
  auto_danmu: boolean
  auto_redpacket: boolean
  danmu_threshold: number
  notification: NotificationSub
  voice_notification: NotificationSub
  pushdeer_notification: NotificationSub
}

export interface CourseConfig extends CourseSettings {
  name: string
}

export type CoursesMap = Record<string, CourseConfig>

export interface PollIntervalSettings {
  poll_interval: number
  default: number
  min: number
  max: number
}

export interface AIKeyEntry {
  name: string
  provider: AIProviderName
  key: string
}

export interface AISettings {
  keys: AIKeyEntry[]
  active_key: number
  fallback_keys: boolean
}

export interface PushdeerKeyEntry {
  name: string
  endpoint: string
  push_key: string
}

export interface PushdeerSettings {
  keys: PushdeerKeyEntry[]
  active_key: number
  language: Language
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
