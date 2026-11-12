import type { AxiosResponse } from 'axios'
import client from './axios'
import { todayInDR, validCalendarDate } from './propertyManagement'
import { validVisitMinutes, VISIT_TIME_ZONE } from './serviceWork'
import { ServiceContractError, ServiceInputError } from './serviceWorkflow'

const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/
const CONTROLS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/
const KINDS = ['milestone', 'visit'] as const
const STATES = ['active', 'snoozed', 'dismissed', 'stopped'] as const
const ATTENTION = ['none', 'upcoming', 'due', 'overdue'] as const
const ACTIONS = ['snooze', 'dismiss', 'restore', 'stop'] as const
export const DUE_WORK_PAGE_SIZE = 30
export const DUE_WORK_LEAD_LIMIT = 30

export type DueWorkKind = typeof KINDS[number]
export type DueWorkAction = typeof ACTIONS[number]
export type DueWorkPreference = { milestones_enabled: boolean; visits_enabled: boolean; lead_days: number; version: number }
type Occurrence = { kind: DueWorkKind; id: string; occurrence_version: number }
export type DueWorkItem = Occurrence & {
  title: string; due_on: string; starts_at: string | null; duration_minutes: number | null
  state: typeof STATES[number]; snoozed_until: string | null; version: number; attention: typeof ATTENTION[number]
}
export type DueWorkPage = {
  project_id: string; as_of: string; time_zone: typeof VISIT_TIME_ZONE; preference: DueWorkPreference
  items: DueWorkItem[]; total: number; page: number; page_size: typeof DUE_WORK_PAGE_SIZE
}

type Common = { projectId: string; submission_id: string; expected_version: number }
export type DueWorkIntent = (Common & Omit<DueWorkPreference, 'version'> & { operation: 'preferences' })
  | (Common & Occurrence & { operation: 'snooze'; snoozed_until: string })
  | (Common & Occurrence & { operation: Exclude<DueWorkAction, 'snooze'> })
export type DueWorkOutcome = { operation: 'preferences'; result: DueWorkPreference } | { operation: DueWorkAction; result: DueWorkItem }

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

export function sameDueWorkOccurrence(left: Occurrence, right: Occurrence): boolean {
  return left.kind === right.kind && left.id === right.id && left.occurrence_version === right.occurrence_version
}

export function dueWorkActions(item: DueWorkItem): readonly DueWorkAction[] {
  if (item.state === 'active') return ['snooze', 'dismiss', 'stop']
  if (item.state === 'snoozed') return ['snooze', 'dismiss', 'restore', 'stop']
  if (item.state === 'dismissed') return ['restore', 'stop']
  return []
}

function preference(value: unknown): DueWorkPreference {
  const row = exact(value, ['milestones_enabled', 'visits_enabled', 'lead_days', 'version'])
  const lead = integer(row.lead_days, 0, DUE_WORK_LEAD_LIMIT), version = integer(row.version, 0)
  if (typeof row.milestones_enabled !== 'boolean' || typeof row.visits_enabled !== 'boolean'
    || (version === 0 && (row.milestones_enabled || row.visits_enabled || lead !== 7))) invalid()
  return { milestones_enabled: row.milestones_enabled, visits_enabled: row.visits_enabled, lead_days: lead, version }
}

function dueWorkItem(value: unknown): DueWorkItem {
  const row = exact(value, ['kind', 'id', 'occurrence_version', 'title', 'due_on', 'starts_at', 'duration_minutes', 'state', 'snoozed_until', 'version', 'attention'])
  const kind = choice(row.kind, KINDS), state = choice(row.state, STATES), attention = choice(row.attention, ATTENTION)
  const version = integer(row.version, 0), dueOn = date(row.due_on), snoozedUntil = row.snoozed_until === null ? null : date(row.snoozed_until)
  const title = row.title, startsAt = row.starts_at, duration = row.duration_minutes
  if (typeof title !== 'string' || title.length === 0 || [...title].length > 180 || CONTROLS.test(title)
    || (state === 'active') === (attention === 'none') || (version === 0 && state !== 'active')
    || (state === 'snoozed') !== (snoozedUntil !== null)) invalid()
  if (kind === 'milestone') {
    if (startsAt !== null || duration !== null) invalid()
  } else if (typeof startsAt !== 'string' || !TIMESTAMP.test(startsAt) || !validCalendarDate(startsAt.slice(0, 10))
    || !Number.isFinite(Date.parse(startsAt)) || todayInDR(new Date(startsAt)) !== dueOn || !validVisitMinutes(duration)) invalid()
  return { kind, id: identifier(row.id), occurrence_version: integer(row.occurrence_version, 1), title, due_on: dueOn,
    starts_at: startsAt as string | null, duration_minutes: duration as number | null, state, snoozed_until: snoozedUntil, version, attention }
}

function privateResponse(response: AxiosResponse<unknown>): unknown {
  const cache = response.headers['cache-control']
  if (response.status !== 200 || typeof cache !== 'string' || !cache.toLowerCase().split(',').some(value => value.trim() === 'no-store')) invalid()
  return response.data
}

const projectPath = (projectId: string) => `/property-network/projects/${identifier(projectId)}/due-work-reminders`
const options = (signal?: AbortSignal) => ({ signal, withCredentials: true, timeout: 15000 })

export async function getDueWorkReminders(projectId: string, page: number, signal?: AbortSignal): Promise<DueWorkPage> {
  integer(page, 1, 1000)
  const id = identifier(projectId)
  const response = await client.get(projectPath(id), { ...options(signal), params: { page } })
  const row = exact(privateResponse(response), ['project_id', 'as_of', 'time_zone', 'preference', 'items', 'total', 'page', 'page_size'])
  if (identifier(row.project_id) !== id || row.time_zone !== VISIT_TIME_ZONE || row.page !== page || row.page_size !== DUE_WORK_PAGE_SIZE
    || !Array.isArray(row.items) || row.items.length > DUE_WORK_PAGE_SIZE) invalid()
  const settings = preference(row.preference), items = row.items.map(dueWorkItem)
  if (new Set(items.map(item => `${item.kind}:${item.id}`)).size !== items.length
    || items.some(item => item.kind === 'milestone' ? !settings.milestones_enabled : !settings.visits_enabled)) invalid()
  return { project_id: id, as_of: date(row.as_of), time_zone: VISIT_TIME_ZONE, preference: settings,
    items, total: integer(row.total, items.length), page, page_size: DUE_WORK_PAGE_SIZE }
}

export function prepareDueWorkIntent(intent: DueWorkIntent) {
  try {
    const path = projectPath(intent.projectId), submission_id = identifier(intent.submission_id)
    const expected_version = integer(intent.expected_version, 0)
    if (intent.operation === 'preferences') {
      if (typeof intent.milestones_enabled !== 'boolean' || typeof intent.visits_enabled !== 'boolean') invalid()
      return { path: `${path}/preferences`, method: 'put' as const,
        body: { submission_id, expected_version, milestones_enabled: intent.milestones_enabled, visits_enabled: intent.visits_enabled,
          lead_days: integer(intent.lead_days, 0, DUE_WORK_LEAD_LIMIT) } }
    }
    const kind = choice(intent.kind, KINDS), id = identifier(intent.id), operation = choice(intent.operation, ACTIONS)
    return { path: `${path}/${kind}/${id}/actions`, method: 'post' as const,
      body: { submission_id, expected_version, occurrence_version: integer(intent.occurrence_version, 1), operation,
        ...(intent.operation === 'snooze' ? { snoozed_until: date(intent.snoozed_until) } : {}) } }
  } catch (error) {
    if (error instanceof ServiceContractError) throw new ServiceInputError()
    throw error
  }
}

function outcome(intent: DueWorkIntent, raw: unknown): DueWorkOutcome {
  if (intent.operation === 'preferences') {
    const result = preference(raw)
    if (result.version <= intent.expected_version || (result.version === intent.expected_version + 1
      && (result.milestones_enabled !== intent.milestones_enabled || result.visits_enabled !== intent.visits_enabled || result.lead_days !== intent.lead_days))) invalid()
    return { operation: intent.operation, result }
  }
  const result = dueWorkItem(raw)
  if (!sameDueWorkOccurrence(intent, result) || result.version <= intent.expected_version) invalid()
  if (result.version === intent.expected_version + 1) {
    const expected = { snooze: 'snoozed', dismiss: 'dismissed', restore: 'active', stop: 'stopped' }[intent.operation]
    const expiredSnooze = intent.operation === 'snooze' && result.state === 'active' && result.snoozed_until === null
    if (!expiredSnooze && (result.state !== expected || (intent.operation === 'snooze' && result.snoozed_until !== intent.snoozed_until))) invalid()
  }
  return { operation: intent.operation, result }
}

export async function runDueWorkIntent(intent: DueWorkIntent, signal?: AbortSignal) {
  const prepared = prepareDueWorkIntent(intent)
  return outcome(intent, privateResponse(await client[prepared.method](prepared.path, prepared.body, options(signal))))
}

export async function resolveDueWorkIntent(intent: DueWorkIntent, signal?: AbortSignal) {
  prepareDueWorkIntent(intent)
  const response = await client.get(`${projectPath(intent.projectId)}/submissions/${identifier(intent.submission_id)}`, options(signal))
  const row = exact(privateResponse(response), ['operation', 'result'])
  if (row.operation !== intent.operation) invalid()
  return outcome(intent, row.result)
}
