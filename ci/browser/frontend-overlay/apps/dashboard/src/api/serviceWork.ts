import client from './axios'
import { ServiceContractError, ServiceInputError } from './serviceWorkflow'

export const WORK_OPERATIONS = ['propose_visit', 'accept_visit', 'decline_visit', 'cancel_visit', 'request_completion', 'request_changes', 'approve_completion'] as const
const WORK_STATES = ['unscheduled', 'proposed', 'confirmed', 'cancelled', 'completion_requested', 'completed'] as const
export const WORK_NOTE_LIMIT = 2000
export const MIN_VISIT_MINUTES = 15
export const MAX_VISIT_MINUTES = 480
export const VISIT_TIME_ZONE = 'America/Santo_Domingo'
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i

export type WorkOperation = typeof WORK_OPERATIONS[number]
type Visit = { starts_at: string; duration_minutes: number }
export type ServiceWork = {
  request_id: string
  project_id: string
  version: number
  status: typeof WORK_STATES[number]
  confirmed_visit: Visit | null
  proposed_visit: (Visit & { proposed_by: 'owner' | 'provider' }) | null
  completion_note: string | null
  completed_on: string | null
  completion_review_note: string | null
  completion_update_id: string | null
  actions: WorkOperation[]
}

export type WorkIntent =
  | ({ operation: 'propose_visit' } & Visit)
  | { operation: 'request_completion'; note: string; completed_on: string }
  | { operation: 'request_changes'; note: string }
  | { operation: 'approve_completion'; share_in_history: true }
  | { operation: 'accept_visit' | 'decline_visit' | 'cancel_visit' }

export type WorkAction = {
  requestId: string
  projectId: string
  payload: WorkIntent & { submission_id: string; expected_version: number }
}

function invalid(): never { throw new ServiceContractError() }
function id(value: unknown) { return typeof value === 'string' && UUID.test(value) ? value.toLowerCase() : invalid() }
function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== keys.length || !keys.every(key => Object.prototype.hasOwnProperty.call(value, key))) invalid()
  return value as Record<string, unknown>
}
function choice<T extends string>(value: unknown, values: readonly T[]): T {
  return typeof value === 'string' && values.includes(value as T) ? value as T : invalid()
}

export function validWorkDay(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
}
export function validWorkNote(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && [...value].length <= WORK_NOTE_LIMIT
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)
}
export function validVisitMinutes(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= MIN_VISIT_MINUTES && value <= MAX_VISIT_MINUTES
}
function validVisitTimestamp(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && validWorkDay(value.slice(0, 10)) && Number.isFinite(Date.parse(value))
}
export function santoDomingoVisitStart(value: string): string | null {
  const timestamp = `${value}:00-04:00`
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) && validVisitTimestamp(timestamp) ? timestamp : null
}
function visit(row: Record<string, unknown>): Visit {
  if (!validVisitTimestamp(row.starts_at) || !validVisitMinutes(row.duration_minutes)) invalid()
  return { starts_at: row.starts_at, duration_minutes: row.duration_minutes }
}
function version(value: unknown): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : invalid()
}

export function decodeServiceWork(value: unknown, requestId: string, projectId: string): ServiceWork {
  const row = object(value, ['request_id', 'project_id', 'version', 'status', 'confirmed_visit', 'proposed_visit', 'completion_note', 'completed_on', 'completion_review_note', 'completion_update_id', 'actions'])
  if (id(row.request_id) !== id(requestId) || id(row.project_id) !== id(projectId) || !Array.isArray(row.actions)) invalid()
  const actions = row.actions.map(action => choice(action, WORK_OPERATIONS))
  if (new Set(actions).size !== actions.length) invalid()
  const proposed = row.proposed_visit === null ? null : object(row.proposed_visit, ['starts_at', 'duration_minutes', 'proposed_by'])
  if (row.completion_note !== null && !validWorkNote(row.completion_note)) invalid()
  if (row.completion_review_note !== null && !validWorkNote(row.completion_review_note)) invalid()
  if (row.completed_on !== null && !validWorkDay(row.completed_on)) invalid()
  return {
    request_id: id(row.request_id), project_id: id(row.project_id), version: version(row.version), status: choice(row.status, WORK_STATES),
    confirmed_visit: row.confirmed_visit === null ? null : visit(object(row.confirmed_visit, ['starts_at', 'duration_minutes'])),
    proposed_visit: proposed === null ? null : { ...visit(proposed), proposed_by: choice(proposed.proposed_by, ['owner', 'provider']) },
    completion_note: row.completion_note, completed_on: row.completed_on, completion_review_note: row.completion_review_note,
    completion_update_id: row.completion_update_id === null ? null : id(row.completion_update_id), actions,
  }
}

export function prepareServiceWorkAction(action: WorkAction) {
  try {
    id(action.requestId); id(action.projectId)
    const { payload } = action
    id(payload.submission_id); version(payload.expected_version)
    const common = ['submission_id', 'expected_version', 'operation']
    switch (payload.operation) {
      case 'propose_visit':
        object(payload, [...common, 'starts_at', 'duration_minutes'])
        if (!validVisitTimestamp(payload.starts_at) || !payload.starts_at.endsWith('-04:00') || !validVisitMinutes(payload.duration_minutes)) invalid()
        break
      case 'request_completion':
        object(payload, [...common, 'note', 'completed_on'])
        if (!validWorkNote(payload.note) || !validWorkDay(payload.completed_on)) invalid()
        break
      case 'request_changes':
        object(payload, [...common, 'note'])
        if (!validWorkNote(payload.note)) invalid()
        break
      case 'approve_completion':
        object(payload, [...common, 'share_in_history'])
        if (payload.share_in_history !== true) invalid()
        break
      case 'accept_visit': case 'decline_visit': case 'cancel_visit':
        object(payload, common)
        break
      default: invalid()
    }
    return payload
  } catch { throw new ServiceInputError() }
}

const path = (requestId: string) => `/property-network/service-requests/${id(requestId)}/work`
const config = (signal?: AbortSignal) => ({ signal, withCredentials: true, timeout: 15000 })

export async function getServiceWork(requestId: string, projectId: string, signal?: AbortSignal) {
  id(projectId)
  const response = await client.get(path(requestId), config(signal))
  return decodeServiceWork(response.data, requestId, projectId)
}
function decodeAppliedWork(value: unknown, action: WorkAction) {
  const work = decodeServiceWork(value, action.requestId, action.projectId)
  if (work.version <= action.payload.expected_version) invalid()
  return work
}
export async function runServiceWorkAction(action: WorkAction, signal: AbortSignal) {
  const response = await client.post(path(action.requestId), prepareServiceWorkAction(action), config(signal))
  return decodeAppliedWork(response.data, action)
}
export async function resolveServiceWorkAction(action: WorkAction, signal: AbortSignal) {
  const response = await client.get(`${path(action.requestId)}/submissions/${id(action.payload.submission_id)}`, config(signal))
  return decodeAppliedWork(response.data, action)
}
