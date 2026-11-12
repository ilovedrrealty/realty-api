import client from './axios'
import { validCalendarDate } from './propertyManagement'
import { VISIT_TIME_ZONE } from './serviceWork'
import { ServiceContractError } from './serviceWorkflow'

export const SERVICE_OPERATIONS_STAGES = [
  'awaiting_replies', 'quote_ready', 'counter_pending', 'offer_expired', 'ready_to_quote', 'invitation',
  'quote_submitted', 'counter_ready', 'schedule_needed', 'visit_response', 'visit_proposed', 'visit_confirmed',
  'visit_completion_due', 'completion_review', 'counter_accepted', 'invitation_unavailable', 'request_needs_attention',
] as const

const VISIT_ACTIONS = ['propose_visit', 'accept_visit', 'decline_visit', 'cancel_visit'] as const
export const SERVICE_OPERATIONS_ACTIONS = {
  managed: [...VISIT_ACTIONS, 'compare_quotes', 'award', 'counter', 'withdraw_offer', 'approve_completion', 'request_changes', 'cancel_request'],
  received: [...VISIT_ACTIONS, 'accept_invitation', 'decline_invitation', 'submit_quote', 'withdraw_offer', 'accept_counter',
    'decline_counter', 'request_completion'],
} as const

const VIEWS = ['managed', 'received'] as const
const SOURCE_KINDS = ['private', 'listing'] as const
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/
const PAGE_SIZE = 30

export type ServiceOperationsView = typeof VIEWS[number]
export type ServiceOperationsSource = { kind: typeof SOURCE_KINDS[number]; id: string }
export type ServiceOperationsScope =
  | { view: 'managed'; page: number; source?: ServiceOperationsSource }
  | { view: 'received'; page: number; source?: never }

type Stage = typeof SERVICE_OPERATIONS_STAGES[number]
type Action = typeof SERVICE_OPERATIONS_ACTIONS[ServiceOperationsView][number]

export type ServiceOperationsItem = {
  request_id: string
  title: string
  stage: Stage
  desired_start_on: string | null
  quote_valid_until: string | null
  starts_at: string | null
  actions: Action[]
  source: (ServiceOperationsSource & { title: string }) | null
}

type ServiceOperationsPage = {
  view: ServiceOperationsView
  as_of: string
  time_zone: typeof VISIT_TIME_ZONE
  items: ServiceOperationsItem[]
  total: number
  page: number
  page_size: typeof PAGE_SIZE
}

function invalid(): never { throw new ServiceContractError() }

function exact(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== keys.length
    || !keys.every(key => Object.prototype.hasOwnProperty.call(value, key))) invalid()
  return value as Record<string, unknown>
}

function choice<T extends string>(value: unknown, values: readonly T[]): T {
  return typeof value === 'string' && values.includes(value as T) ? value as T : invalid()
}

function uuid(value: unknown): string {
  return typeof value === 'string' && UUID.test(value) ? value.toLowerCase() : invalid()
}

function title(value: unknown, limit: number): string {
  return typeof value === 'string' && value.trim().length > 0 && [...value].length <= limit
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value) ? value : invalid()
}

function date(value: unknown): string {
  return typeof value === 'string' && validCalendarDate(value) ? value : invalid()
}

function visitStart(value: unknown): string | null {
  if (value === null) return null
  if (typeof value !== 'string' || !TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) invalid()
  date(value.slice(0, 10))
  return value
}

function decodeItem(value: unknown, scope: ServiceOperationsScope): ServiceOperationsItem {
  const row = exact(value, ['request_id', 'title', 'stage', 'desired_start_on', 'quote_valid_until', 'starts_at', 'actions', 'source'])
  const actionsForView: readonly Action[] = SERVICE_OPERATIONS_ACTIONS[scope.view]
  if (!Array.isArray(row.actions) || row.actions.length > actionsForView.length) invalid()
  const actions = row.actions.map(action => choice(action, actionsForView))
  if (new Set(actions).size !== actions.length) invalid()

  let source: ServiceOperationsItem['source'] = null
  if (scope.view === 'managed') {
    const data = exact(row.source, ['kind', 'id', 'title'])
    source = { kind: choice(data.kind, SOURCE_KINDS), id: uuid(data.id), title: title(data.title, 500) }
    if (scope.source && (source.kind !== scope.source.kind || source.id !== uuid(scope.source.id))) invalid()
  } else if (row.source !== null) invalid()

  return { request_id: uuid(row.request_id), title: title(row.title, 180), stage: choice(row.stage, SERVICE_OPERATIONS_STAGES),
    desired_start_on: row.desired_start_on === null ? null : date(row.desired_start_on),
    quote_valid_until: row.quote_valid_until === null ? null : date(row.quote_valid_until),
    starts_at: visitStart(row.starts_at), actions, source }
}

export async function getServiceOperations(scope: ServiceOperationsScope, signal?: AbortSignal): Promise<ServiceOperationsPage> {
  const view = choice(scope.view, VIEWS)
  if (!Number.isSafeInteger(scope.page) || scope.page < 1 || scope.page > 1000 || (view === 'received' && scope.source)) invalid()
  const params = { view, page: scope.page,
    ...(scope.source ? { source_kind: choice(scope.source.kind, SOURCE_KINDS), source_id: uuid(scope.source.id) } : {}) }
  const response = await client.get<unknown>('/property-network/service-operations', { params, withCredentials: true, timeout: 15000, signal })
  const cacheControl = response.headers['cache-control']
  if (typeof cacheControl !== 'string' || !cacheControl.toLowerCase().split(',').some(value => value.trim() === 'no-store')) invalid()
  const row = exact(response.data, ['view', 'as_of', 'time_zone', 'items', 'total', 'page', 'page_size'])
  if (row.view !== view || row.time_zone !== VISIT_TIME_ZONE || row.page !== scope.page || row.page_size !== PAGE_SIZE
    || typeof row.total !== 'number' || !Number.isSafeInteger(row.total) || row.total < 0
    || !Array.isArray(row.items) || row.items.length > Math.min(PAGE_SIZE, row.total)) invalid()
  const items = row.items.map(item => decodeItem(item, scope))
  if (new Set(items.map(item => item.request_id)).size !== items.length) invalid()
  return { view, as_of: date(row.as_of), time_zone: VISIT_TIME_ZONE, items, total: row.total, page: scope.page, page_size: PAGE_SIZE }
}
