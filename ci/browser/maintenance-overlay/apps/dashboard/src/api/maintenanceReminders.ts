import type { AxiosResponse } from 'axios'
import client from './axios'
import { validCalendarDate } from './propertyManagement'
import { ServiceContractError, ServiceInputError } from './serviceWorkflow'

const PREFIX = '/property-network/maintenance-reminders'
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const EDGE_WHITESPACE = /^\p{White_Space}+|\p{White_Space}+$/gu
const CONTROLS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/
export const REMINDER_REPEATS = ['none', 'weekly', 'monthly', 'quarterly', 'yearly'] as const
export const REMINDER_ACTIONS = ['complete', 'snooze', 'pause', 'resume', 'stop'] as const
export const REMINDER_TITLE_LIMIT = 180
export const REMINDER_LEAD_LIMIT = 30
export const REMINDER_PAGE_SIZE = 30
const STATUSES = ['active', 'paused', 'completed', 'stopped'] as const
const ATTENTION = ['none', 'scheduled', 'upcoming', 'due', 'overdue', 'snoozed'] as const

export type ReminderSource = { kind: 'private' | 'listing'; id: string }
export type ReminderRepeat = typeof REMINDER_REPEATS[number]
export type ReminderAction = typeof REMINDER_ACTIONS[number]
export type ReminderPreference = { enabled: boolean; lead_days: number; version: number }
export type MaintenanceReminder = {
  id: string; title: string; repeat: ReminderRepeat; anchor_on: string; due_on: string; snoozed_until: string | null
  status: typeof STATUSES[number]; version: number; attention: typeof ATTENTION[number]
}
export type MaintenanceReminderPage = {
  source: ReminderSource; as_of: string; time_zone: 'America/Santo_Domingo'; preference: ReminderPreference
  items: MaintenanceReminder[]; total: number; page: number; page_size: typeof REMINDER_PAGE_SIZE
}

type Common = { source: ReminderSource; submission_id: string }
type ScheduleChange = Common & { schedule_id: string; expected_version: number }
export type ReminderIntent = (Common & { operation: 'preferences'; expected_version: number; enabled: boolean; lead_days: number })
  | (Common & { operation: 'create'; title: string; repeat: ReminderRepeat; first_due_on: string })
  | (ScheduleChange & { operation: 'snooze'; snoozed_until: string })
  | (ScheduleChange & { operation: Exclude<ReminderAction, 'snooze'> })

export type ReminderOutcome = { operation: 'preferences'; result: ReminderPreference }
  | { operation: 'create' | ReminderAction; result: MaintenanceReminder }

function invalid(): never { throw new ServiceContractError() }

function identifier(value: unknown): string {
  return typeof value === 'string' && UUID.test(value) ? value.toLowerCase() : invalid()
}

function integer(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= min && value <= max ? value : invalid()
}

function choice<T extends string>(value: unknown, values: readonly T[]): T {
  return typeof value === 'string' && values.includes(value as T) ? value as T : invalid()
}

function exact(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== fields.length
    || !fields.every(field => Object.prototype.hasOwnProperty.call(value, field))) invalid()
  return value as Record<string, unknown>
}

function date(value: unknown): string {
  return typeof value === 'string' && validCalendarDate(value) ? value : invalid()
}

function validStoredTitle(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && [...value].length <= REMINDER_TITLE_LIMIT && !CONTROLS.test(value)
}

export function normalizeReminderTitle(value: string): string | null {
  if (typeof value !== 'string' || CONTROLS.test(value)) return null
  const title = value.replace(EDGE_WHITESPACE, '')
  return validStoredTitle(title) ? title : null
}

export function reminderActions(item: MaintenanceReminder): readonly ReminderAction[] {
  if (item.status === 'active') return ['complete', 'snooze', 'pause', 'stop']
  if (item.status === 'paused') return ['resume', 'stop']
  return []
}

function preference(value: unknown): ReminderPreference {
  const row = exact(value, ['enabled', 'lead_days', 'version'])
  const leadDays = integer(row.lead_days, 0, REMINDER_LEAD_LIMIT), version = integer(row.version, 0)
  if (typeof row.enabled !== 'boolean' || (version === 0 && (row.enabled || leadDays !== 7))) invalid()
  return { enabled: row.enabled, lead_days: leadDays, version }
}

function reminder(value: unknown): MaintenanceReminder {
  const row = exact(value, ['id', 'title', 'repeat', 'anchor_on', 'due_on', 'snoozed_until', 'status', 'version', 'attention'])
  const title = row.title
  const status = choice(row.status, STATUSES), attention = choice(row.attention, ATTENTION)
  const anchorOn = date(row.anchor_on), dueOn = date(row.due_on)
  if (!validStoredTitle(title) || dueOn < anchorOn
    || (status !== 'active' && attention !== 'none')) invalid()
  return { id: identifier(row.id), title, repeat: choice(row.repeat, REMINDER_REPEATS), anchor_on: anchorOn, due_on: dueOn,
    snoozed_until: row.snoozed_until === null ? null : date(row.snoozed_until), status, version: integer(row.version, 1), attention }
}

function privateResponse(response: AxiosResponse<unknown>, status: number): unknown {
  const cache = response.headers['cache-control']
  if (response.status !== status || typeof cache !== 'string' || !cache.toLowerCase().split(',').some(value => value.trim() === 'no-store')) invalid()
  return response.data
}

function sourcePath(source: ReminderSource) {
  const kind = choice(source.kind, ['private', 'listing'])
  return `${PREFIX}/${kind}/${identifier(source.id)}`
}

const options = (signal?: AbortSignal) => ({ signal, withCredentials: true, timeout: 15000 })

export async function getMaintenanceReminders(source: ReminderSource, page: number, signal?: AbortSignal): Promise<MaintenanceReminderPage> {
  integer(page, 1, 1000)
  const response = await client.get(sourcePath(source), { ...options(signal), params: { page } })
  const row = exact(privateResponse(response, 200), ['source', 'as_of', 'time_zone', 'preference', 'items', 'total', 'page', 'page_size'])
  const identity = exact(row.source, ['kind', 'id'])
  if (identity.kind !== source.kind || identifier(identity.id) !== identifier(source.id) || row.time_zone !== 'America/Santo_Domingo'
    || row.page !== page || row.page_size !== REMINDER_PAGE_SIZE || !Array.isArray(row.items) || row.items.length > REMINDER_PAGE_SIZE) invalid()
  const items = row.items.map(reminder), settings = preference(row.preference)
  if (new Set(items.map(item => item.id)).size !== items.length || (!settings.enabled && items.some(item => item.attention !== 'none'))) invalid()
  return { source: { kind: source.kind, id: identifier(source.id) }, as_of: date(row.as_of), time_zone: row.time_zone,
    preference: settings, items, total: integer(row.total, items.length), page, page_size: REMINDER_PAGE_SIZE }
}

export function prepareReminderIntent(intent: ReminderIntent) {
  try {
    const path = sourcePath(intent.source), submission_id = identifier(intent.submission_id)
    if (intent.operation === 'preferences') {
      if (typeof intent.enabled !== 'boolean') invalid()
      return { path: `${path}/preferences`, method: 'put' as const, status: 200,
        body: { submission_id, expected_version: integer(intent.expected_version, 0), enabled: intent.enabled,
          lead_days: integer(intent.lead_days, 0, REMINDER_LEAD_LIMIT) } }
    }
    if (intent.operation === 'create') {
      const title = normalizeReminderTitle(intent.title)
      if (!title || title !== intent.title) invalid()
      return { path, method: 'post' as const, status: 201,
        body: { submission_id, title, repeat: choice(intent.repeat, REMINDER_REPEATS), first_due_on: date(intent.first_due_on) } }
    }
    const operation = choice(intent.operation, REMINDER_ACTIONS)
    return { path: `${path}/${identifier(intent.schedule_id)}/actions`, method: 'post' as const, status: 200,
      body: { submission_id, expected_version: integer(intent.expected_version, 1), operation,
        ...(intent.operation === 'snooze' ? { snoozed_until: date(intent.snoozed_until) } : {}) } }
  } catch (error) {
    if (error instanceof ServiceContractError) throw new ServiceInputError()
    throw error
  }
}

function outcome(intent: ReminderIntent, raw: unknown): ReminderOutcome {
  if (intent.operation === 'preferences') {
    const result = preference(raw)
    if (result.version <= intent.expected_version || (result.version === intent.expected_version + 1
      && (result.enabled !== intent.enabled || result.lead_days !== intent.lead_days))) invalid()
    return { operation: intent.operation, result }
  }
  const result = reminder(raw)
  if (intent.operation === 'create') {
    if (result.title !== intent.title || result.repeat !== intent.repeat || result.anchor_on !== intent.first_due_on) invalid()
  } else {
    if (result.id !== identifier(intent.schedule_id) || result.version <= intent.expected_version) invalid()
    if (result.version === intent.expected_version + 1) {
      const status = intent.operation === 'pause' ? 'paused' : intent.operation === 'stop' ? 'stopped'
        : intent.operation === 'complete' && result.repeat === 'none' ? 'completed' : 'active'
      if (result.status !== status || (intent.operation === 'snooze' && result.snoozed_until !== intent.snoozed_until)) invalid()
    }
  }
  return { operation: intent.operation, result }
}

export async function runReminderIntent(intent: ReminderIntent, signal?: AbortSignal) {
  const prepared = prepareReminderIntent(intent)
  const response = await client[prepared.method](prepared.path, prepared.body, options(signal))
  return outcome(intent, privateResponse(response, prepared.status))
}

export async function resolveReminderIntent(intent: ReminderIntent, signal?: AbortSignal) {
  prepareReminderIntent(intent)
  const response = await client.get(`${sourcePath(intent.source)}/submissions/${identifier(intent.submission_id)}`, options(signal))
  const row = exact(privateResponse(response, 200), ['operation', 'result'])
  if (row.operation !== intent.operation) invalid()
  return outcome(intent, row.result)
}
