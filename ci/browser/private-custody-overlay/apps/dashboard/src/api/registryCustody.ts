import client from './axios'
import { PRIVATE_PROPERTY_CLASSES, type PrivatePropertyFields } from './propertyNetwork'
import { validCalendarDate } from './propertyManagement'
import { ServiceContractError, ServiceInputError } from './serviceWorkflow'

const BASE = '/property-network/registry-custody'
const VIEWS = ['sent', 'received'] as const
const ACTIONS = ['accept', 'decline', 'cancel'] as const
const STATUSES = ['pending', 'accepted', 'declined', 'cancelled', 'expired'] as const
const RELATIONSHIPS = ['owner', 'manager', 'seller'] as const
const MAX_MEMBER_CODE = 2147483647
const PAGE_SIZE = 30
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/
export const CUSTODY_LABEL_MAX = 120

export type CustodyView = typeof VIEWS[number]
export type CustodyAction = typeof ACTIONS[number]
type Status = typeof STATUSES[number]
export type CustodyFields = Pick<PrivatePropertyFields, 'title' | 'location' | 'address' | 'property_class' | 'relationship'>
export type CustodyOptions = {
  property_id: string; property_version: number; fields: CustodyFields
  blockers: { active_projects: number; open_requests: number; unfinished_awards: number }
  pending_transfer_id: string | null
}
export type CustodyTransfer = { id: string; label: string; view: CustodyView; status: Status; version: number; expires_at: string
  fields: CustodyFields | null; property_id: string | null; actions: CustodyAction[] }
export type CustodyOffer = { operation: 'offer'; submission_id: string; property_id: string; expected_property_version: number
  recipient_user_code: number; label: string; transfer_acknowledged: true }
export type CustodyDecision = { operation: CustodyAction; submission_id: string; id: string; expected_version: number
  transfer_acknowledged?: true }
type Intent = CustodyOffer | CustodyDecision
type Receipt = { id: string; operation: Intent['operation']; status: Status; version: number }

const statusFor = { offer: 'pending', accept: 'accepted', decline: 'declined', cancel: 'cancelled' } as const
const config = (signal?: AbortSignal) => ({ signal, withCredentials: true, timeout: 15_000 })
function invalid(): never { throw new ServiceContractError() }
function exact(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== keys.length
    || !keys.every(key => Object.prototype.hasOwnProperty.call(value, key))) invalid()
  return value as Record<string, unknown>
}
function choice<T extends string>(value: unknown, allowed: readonly T[]): T {
  return allowed.includes(value as T) ? value as T : invalid()
}
function id(value: unknown): string {
  return typeof value === 'string' && UUID.test(value) ? value.toLowerCase() : invalid()
}
function integer(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= min && value <= max ? value : invalid()
}
function text(value: unknown, max: number, min = 1): string {
  return typeof value === 'string' && [...value].length >= min && [...value].length <= max ? value : invalid()
}
export function validCustodyLabel(value: string) {
  const trimmed = value.trim()
  return trimmed.length > 0 && [...trimmed].length <= CUSTODY_LABEL_MAX && !/[\u0000-\u001f\u007f]/.test(trimmed)
}
export function custodyMemberCode(value: string) {
  if (!/^[1-9]\d{0,9}$/.test(value)) return null
  const code = Number(value)
  return code <= MAX_MEMBER_CODE ? code : null
}
export function custodyBlocked(options: CustodyOptions) {
  const { active_projects, open_requests, unfinished_awards } = options.blockers
  return !!(active_projects || open_requests || unfinished_awards || options.pending_transfer_id)
}
function privateBody(response: { status: number; headers: { [name: string]: unknown }; data: unknown }, status: number) {
  const cache = response.headers['cache-control']
  if (response.status !== status || typeof cache !== 'string'
    || !cache.toLowerCase().split(',').some(value => value.trim() === 'no-store')) invalid()
  return response.data
}
function expiry(value: unknown): string {
  if (typeof value !== 'string' || !TIMESTAMP.test(value) || !validCalendarDate(value.slice(0, 10))
    || !Number.isFinite(Date.parse(value))) invalid()
  return value
}
function fields(value: unknown): CustodyFields {
  const row = exact(value, ['title', 'location', 'address', 'property_class', 'relationship'])
  return { title: text(row.title, 180), location: text(row.location, 180), address: text(row.address, 500, 0),
    property_class: choice(row.property_class, PRIVATE_PROPERTY_CLASSES), relationship: choice(row.relationship, RELATIONSHIPS) }
}
function transfer(value: unknown, view: CustodyView, detail: boolean): CustodyTransfer {
  const row = exact(value, ['id', 'label', 'view', 'status', 'version', 'expires_at', 'fields', 'property_id', 'actions'])
  if (row.view !== view) invalid()
  const status = choice(row.status, STATUSES)
  const version = integer(row.version, 1, 2)
  // Expiry is projected at v1 and becomes a persisted v2 when a new offer replaces it.
  if (status !== 'expired' && version !== (status === 'pending' ? 1 : 2)) invalid()
  const expected: CustodyAction[] = status !== 'pending' ? [] : view === 'sent' ? ['cancel'] : ['accept', 'decline']
  const actions = row.actions
  if (!Array.isArray(actions) || actions.length !== expected.length
    || !expected.every(action => actions.includes(action))) invalid()
  const revealFields = detail && (view === 'sent' || status === 'accepted' && (row.fields !== null || row.property_id !== null))
  if (!revealFields && (row.fields !== null || row.property_id !== null)) invalid()
  const label = text(row.label, CUSTODY_LABEL_MAX)
  if (!validCustodyLabel(label) || label !== label.trim()) invalid()
  return { id: id(row.id), label, view, status, version, expires_at: expiry(row.expires_at),
    fields: revealFields ? fields(row.fields) : null, property_id: revealFields ? id(row.property_id) : null, actions: expected }
}

export async function getCustodyOptions(propertyId: string, signal?: AbortSignal): Promise<CustodyOptions> {
  const identity = id(propertyId)
  const response = await client.get<unknown>(`${BASE}/options/${identity}`, config(signal))
  const row = exact(privateBody(response, 200), ['property_id', 'property_version', 'fields', 'blockers', 'pending_transfer_id'])
  if (id(row.property_id) !== identity) invalid()
  const blockers = exact(row.blockers, ['active_projects', 'open_requests', 'unfinished_awards'])
  return { property_id: identity, property_version: integer(row.property_version, 1), fields: fields(row.fields),
    blockers: { active_projects: integer(blockers.active_projects, 0), open_requests: integer(blockers.open_requests, 0),
      unfinished_awards: integer(blockers.unfinished_awards, 0) },
    pending_transfer_id: row.pending_transfer_id === null ? null : id(row.pending_transfer_id) }
}

export async function getCustodyTransfers(view: CustodyView, page: number, signal?: AbortSignal) {
  choice(view, VIEWS)
  integer(page, 1, 1000)
  const response = await client.get<unknown>(BASE, { ...config(signal), params: { view, page } })
  const row = exact(privateBody(response, 200), ['view', 'items', 'total', 'page', 'page_size'])
  if (row.view !== view || row.page !== page || row.page_size !== PAGE_SIZE || !Array.isArray(row.items)
    || row.items.length > PAGE_SIZE) invalid()
  const items = row.items.map(item => transfer(item, view, false))
  if (new Set(items.map(item => item.id)).size !== items.length) invalid()
  return { view, items, total: integer(row.total, items.length), page, page_size: PAGE_SIZE }
}

export async function getCustodyTransfer(transferId: string, view: CustodyView, signal?: AbortSignal) {
  const identity = id(transferId)
  choice(view, VIEWS)
  const response = await client.get<unknown>(`${BASE}/${identity}`, config(signal))
  const current = transfer(privateBody(response, 200), view, true)
  return current.id === identity ? current : invalid()
}

export function prepareCustodyTransfer(intent: Intent) {
  try {
    const submission_id = id(intent.submission_id)
    if (intent.operation === 'offer') {
      if (!validCustodyLabel(intent.label) || intent.transfer_acknowledged !== true) invalid()
      return { path: BASE, body: { submission_id, property_id: id(intent.property_id),
        expected_property_version: integer(intent.expected_property_version, 1),
        recipient_user_code: integer(intent.recipient_user_code, 1, MAX_MEMBER_CODE), label: intent.label.trim(),
        transfer_acknowledged: true } }
    }
    const operation = choice(intent.operation, ACTIONS)
    if (operation === 'accept' ? intent.transfer_acknowledged !== true : intent.transfer_acknowledged !== undefined) invalid()
    return { path: `${BASE}/${id(intent.id)}/actions`, body: { submission_id, operation,
      expected_version: integer(intent.expected_version, 1, 1), ...(operation === 'accept' ? { transfer_acknowledged: true } : {}) } }
  } catch { throw new ServiceInputError() }
}

function receipt(value: unknown, intent: Intent): Receipt {
  const row = exact(value, ['id', 'operation', 'status', 'version'])
  const identity = id(row.id), version = intent.operation === 'offer' ? 1 : 2
  if (row.operation !== intent.operation || row.status !== statusFor[intent.operation] || row.version !== version
    || (intent.operation !== 'offer' && identity !== id(intent.id))) invalid()
  return { id: identity, operation: intent.operation, status: statusFor[intent.operation], version }
}
export async function runCustodyTransfer(intent: Intent, signal: AbortSignal) {
  const { path, body } = prepareCustodyTransfer(intent)
  const response = await client.post<unknown>(path, body, config(signal))
  return receipt(privateBody(response, intent.operation === 'offer' ? 201 : 200), intent)
}
export async function resolveCustodyTransfer(intent: Intent, signal: AbortSignal) {
  const response = await client.get<unknown>(`${BASE}/submissions/${id(intent.submission_id)}`, config(signal))
  return receipt(privateBody(response, 200), intent)
}
