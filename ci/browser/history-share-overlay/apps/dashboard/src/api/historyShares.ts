import client from './axios'
import { validCalendarDate } from './propertyManagement'
import { ServiceContractError, ServiceInputError } from './serviceWorkflow'

const BASE = '/property-network/history-shares'
const PAGE_SIZE = 30
const KINDS = ['private', 'listing'] as const
const VIEWS = ['sent', 'received'] as const
const STATUSES = ['pending', 'accepted', 'declined', 'revoked', 'expired'] as const
const ACTIONS = ['accept', 'decline', 'revoke'] as const
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/
export const HISTORY_SHARE_MAX_ITEMS = 25
export const HISTORY_SHARE_MAX_LABEL = 120
export const HISTORY_SHARE_MAX_SUMMARY = 600
export const HISTORY_SHARE_EXPIRIES = [1, 7, 14, 30] as const

export type HistoryShareSource = { kind: typeof KINDS[number]; id: string }
export type HistoryShareView = typeof VIEWS[number]
export type HistoryShareAction = typeof ACTIONS[number]
type Status = typeof STATUSES[number]
type Expiry = typeof HISTORY_SHARE_EXPIRIES[number]
type Page = { total: number; page: number; page_size: typeof PAGE_SIZE }
type Metadata = { occurred_on: string; service_category_ids: string[] }
export type HistoryShareOption = Metadata & { update_id: string; project_title: string }
export type HistoryShareOptions = Page & { source_kind: HistoryShareSource['kind']; source_id: string; anchor_project_id: string;
  professionals: { user_code: number; display_name: string }[]; items: HistoryShareOption[] }
export type HistoryShare = { id: string; label: string; view: HistoryShareView; status: Status; expires_at: string;
  version: number; item_count: number; items: (Metadata & { summary: string })[] | null; actions: HistoryShareAction[] }
type CreateInput = { source_kind: HistoryShareSource['kind']; source_id: string; anchor_project_id: string;
  recipient_user_code: number; label: string; expires_in_days: Expiry; items: { update_id: string; summary: string }[] }
export type HistoryShareCreate = { operation: 'create'; submission_id: string; input: CreateInput }
export type HistoryShareDecision = { operation: HistoryShareAction; submission_id: string; id: string; expected_version: number }
type Intent = HistoryShareCreate | HistoryShareDecision
export type HistoryShareReceipt = { id: string; operation: Intent['operation']; status: Status; version: number }

const expectedStatus = { create: 'pending', accept: 'accepted', decline: 'declined', revoke: 'revoked' } as const
function invalid(): never { throw new ServiceContractError() }
function exact(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== fields.length
    || !fields.every(key => Object.prototype.hasOwnProperty.call(value, key))) invalid()
  return value as Record<string, unknown>
}
function choice<T extends string | number>(value: unknown, allowed: readonly T[]): T {
  return allowed.includes(value as T) ? value as T : invalid()
}
function id(value: unknown): string {
  return typeof value === 'string' && UUID.test(value) ? value.toLowerCase() : invalid()
}
function integer(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= min && value <= max ? value : invalid()
}
function array<T>(value: unknown, max: number, decode: (item: unknown) => T): T[] {
  return Array.isArray(value) && value.length <= max ? value.map(decode) : invalid()
}
function unique<T>(values: T[]): T[] {
  return new Set(values).size === values.length ? values : invalid()
}
export function validHistoryShareText(value: string, max: number): boolean {
  return value.trim().length > 0 && [...value.trim()].length <= max && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value)
}
function text(value: unknown, max: number): string {
  return typeof value === 'string' && validHistoryShareText(value, max) ? value.trim() : invalid()
}
function day(value: unknown): string {
  return typeof value === 'string' && validCalendarDate(value) ? value : invalid()
}
function expiry(value: unknown): string {
  if (typeof value !== 'string' || !TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) invalid()
  day(value.slice(0, 10))
  return value
}
function metadata(row: Record<string, unknown>): Metadata {
  return { occurred_on: day(row.occurred_on), service_category_ids: unique(array(row.service_category_ids, 12, raw =>
    typeof raw === 'string' && /^[a-z][a-z0-9_]{0,79}$/.test(raw) ? raw : invalid())) }
}
function page<T>(row: Record<string, unknown>, wantedPage: number, items: T[]): Page {
  if (row.page !== wantedPage || row.page_size !== PAGE_SIZE) invalid()
  return { total: integer(row.total, items.length), page: wantedPage, page_size: PAGE_SIZE }
}
const config = (signal?: AbortSignal) => ({ withCredentials: true, timeout: 15_000, signal })
function privateBody(response: { status: number; headers: { [name: string]: unknown }; data: unknown }, status: number): unknown {
  const cache = response.headers['cache-control']
  if (response.status !== status || typeof cache !== 'string' || !cache.toLowerCase().split(',').some(item => item.trim() === 'no-store')) invalid()
  return response.data
}

function decodeShare(value: unknown, view: HistoryShareView, detail: boolean): HistoryShare {
  const row = exact(value, ['id', 'label', 'view', 'status', 'expires_at', 'version', 'item_count', 'items', 'actions'])
  const status = choice(row.status, STATUSES), count = integer(row.item_count, 1, HISTORY_SHARE_MAX_ITEMS)
  if (row.view !== view || view === 'received' && status !== 'pending' && status !== 'accepted') invalid()
  const reveal = detail && (view === 'sent' || status === 'accepted')
  if (!reveal && row.items !== null) invalid()
  const items = reveal ? array(row.items, HISTORY_SHARE_MAX_ITEMS, raw => {
    const item = exact(raw, ['occurred_on', 'service_category_ids', 'summary'])
    return { ...metadata(item), summary: text(item.summary, HISTORY_SHARE_MAX_SUMMARY) }
  }) : null
  if (items && items.length !== count) invalid()
  const actions = unique(array(row.actions, 2, action => choice(action, ACTIONS)))
  const expected = status === 'pending' && view === 'received' ? ['accept', 'decline']
    : status === 'pending' || status === 'accepted' ? ['revoke'] : []
  if (actions.length !== expected.length || !actions.every(action => expected.includes(action))) invalid()
  return { id: id(row.id), label: text(row.label, HISTORY_SHARE_MAX_LABEL), view, status, expires_at: expiry(row.expires_at),
    version: integer(row.version, 1, 3), item_count: count, items, actions }
}

export async function getHistoryShares(view: HistoryShareView, pageNumber: number, signal?: AbortSignal) {
  choice(view, VIEWS); integer(pageNumber, 1, 1000)
  const response = await client.get<unknown>(BASE, { ...config(signal), params: { view, page: pageNumber } })
  const row = exact(privateBody(response, 200), ['view', 'items', 'total', 'page', 'page_size'])
  if (row.view !== view) invalid()
  const items = array(row.items, PAGE_SIZE, item => decodeShare(item, view, false))
  unique(items.map(item => item.id))
  return { view, items, ...page(row, pageNumber, items) }
}

export async function getHistoryShare(identity: string, view: HistoryShareView, signal?: AbortSignal): Promise<HistoryShare> {
  choice(view, VIEWS)
  const expectedId = id(identity)
  const response = await client.get<unknown>(`${BASE}/${expectedId}`, config(signal))
  const result = decodeShare(privateBody(response, 200), view, true)
  return result.id === expectedId ? result : invalid()
}

export async function getHistoryShareOptions(source: HistoryShareSource, anchor: string, pageNumber: number,
  signal?: AbortSignal): Promise<HistoryShareOptions> {
  const kind = choice(source.kind, KINDS), sourceId = id(source.id), anchorId = id(anchor)
  integer(pageNumber, 1, 1000)
  const response = await client.get<unknown>(`${BASE}/options/${kind}/${sourceId}`, {
    ...config(signal), params: { anchor_project_id: anchorId, page: pageNumber },
  })
  const row = exact(privateBody(response, 200), ['source_kind', 'source_id', 'anchor_project_id', 'professionals', 'items', 'total', 'page', 'page_size'])
  if (row.source_kind !== kind || id(row.source_id) !== sourceId || id(row.anchor_project_id) !== anchorId) invalid()
  const professionals = array(row.professionals, Number.MAX_SAFE_INTEGER, raw => {
    const item = exact(raw, ['user_code', 'display_name'])
    return { user_code: integer(item.user_code, 1, 2147483647), display_name: text(item.display_name, 200) }
  })
  const items = array(row.items, PAGE_SIZE, raw => {
    const item = exact(raw, ['update_id', 'project_title', 'occurred_on', 'service_category_ids'])
    return { update_id: id(item.update_id), project_title: text(item.project_title, 180), ...metadata(item) }
  })
  unique(professionals.map(item => item.user_code)); unique(items.map(item => item.update_id))
  return { source_kind: kind, source_id: sourceId, anchor_project_id: anchorId, professionals, items, ...page(row, pageNumber, items) }
}

export function prepareHistoryShare(intent: Intent) {
  try {
    const submission_id = id(intent.submission_id)
    if (intent.operation !== 'create') return { path: `${BASE}/${id(intent.id)}/actions`, body: { submission_id,
      operation: choice(intent.operation, ACTIONS), expected_version: integer(intent.expected_version, 1, 3) } }
    const input = intent.input
    const items = array(input.items, HISTORY_SHARE_MAX_ITEMS, raw => {
      const item = exact(raw, ['update_id', 'summary'])
      return { update_id: id(item.update_id), summary: text(item.summary, HISTORY_SHARE_MAX_SUMMARY) }
    })
    if (!items.length) invalid()
    unique(items.map(item => item.update_id))
    return { path: BASE, body: { submission_id, source_kind: choice(input.source_kind, KINDS), source_id: id(input.source_id),
      anchor_project_id: id(input.anchor_project_id), recipient_user_code: integer(input.recipient_user_code, 1, 2147483647),
      label: text(input.label, HISTORY_SHARE_MAX_LABEL), expires_in_days: choice(input.expires_in_days, HISTORY_SHARE_EXPIRIES), items } }
  } catch { throw new ServiceInputError() }
}

function receipt(value: unknown, intent: Intent): HistoryShareReceipt {
  const row = exact(value, ['id', 'operation', 'status', 'version']), identity = id(row.id)
  const status = expectedStatus[intent.operation], version = intent.operation === 'create' ? 1 : intent.expected_version + 1
  if (row.operation !== intent.operation || row.status !== status || row.version !== version
    || (intent.operation !== 'create' && identity !== id(intent.id))) invalid()
  return { id: identity, operation: intent.operation, status, version }
}

export async function runHistoryShare(intent: Intent, signal: AbortSignal): Promise<HistoryShareReceipt> {
  const { path, body } = prepareHistoryShare(intent)
  const response = await client.post<unknown>(path, body, config(signal))
  return receipt(privateBody(response, intent.operation === 'create' ? 201 : 200), intent)
}

export async function resolveHistoryShare(intent: Intent, signal: AbortSignal): Promise<HistoryShareReceipt> {
  const response = await client.get<unknown>(`${BASE}/submissions/${id(intent.submission_id)}`, config(signal))
  return receipt(privateBody(response, 200), intent)
}
