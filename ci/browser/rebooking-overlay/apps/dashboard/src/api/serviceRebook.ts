import client from './axios'
import { PRIVATE_PROPERTY_CLASSES } from './propertyNetwork'
import { ServiceContractError } from './serviceWorkflow'

const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const CATEGORY = /^[a-z][a-z0-9_]{0,79}$/
const CONTROLS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/

export type ServiceRebookScope = {
  request_id: string
  category_id: string
  source: { kind: 'private' | 'listing'; id: string; property_class: string }
}

export type ServiceRebookContext = Omit<ServiceRebookScope, 'source'> & {
  source: ServiceRebookScope['source'] & { title: string }
  provider: { user_code: number; member_alias: string }
}

function invalid(): never { throw new ServiceContractError() }

function identifier(value: unknown): string {
  return typeof value === 'string' && UUID.test(value) ? value.toLowerCase() : invalid()
}

function exactObject(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== fields.length
    || !fields.every(field => Object.prototype.hasOwnProperty.call(value, field))) invalid()
  return value as Record<string, unknown>
}

function label(value: unknown, max: number): string {
  return typeof value === 'string' && !!value.trim() && [...value].length <= max && !CONTROLS.test(value) ? value : invalid()
}

export function sameRebookScope(left: ServiceRebookScope, right: ServiceRebookScope): boolean {
  return left.request_id === right.request_id && left.category_id === right.category_id && left.source.kind === right.source.kind
    && left.source.id === right.source.id && left.source.property_class === right.source.property_class
}

export function sameRebookSelection(left: ServiceRebookContext, right: ServiceRebookContext): boolean {
  return sameRebookScope(left, right) && left.provider.user_code === right.provider.user_code
}

export function sameRebookPreview(left: ServiceRebookContext, right: ServiceRebookContext): boolean {
  return sameRebookSelection(left, right) && left.source.title === right.source.title
    && left.provider.member_alias === right.provider.member_alias
}

export async function getServiceRebookContext(requestId: string, signal?: AbortSignal): Promise<ServiceRebookContext> {
  const id = identifier(requestId)
  const response = await client.get(`/property-network/service-requests/${id}/rebook-context`, { signal, withCredentials: true, timeout: 15000 })
  const cache = response.headers['cache-control']
  if (typeof cache !== 'string' || !cache.toLowerCase().split(',').some(value => value.trim() === 'no-store')) invalid()
  const row = exactObject(response.data, ['request_id', 'source', 'category_id', 'provider'])
  const source = exactObject(row.source, ['kind', 'id', 'title', 'property_class'])
  const provider = exactObject(row.provider, ['user_code', 'member_alias'])
  const propertyClass = PRIVATE_PROPERTY_CLASSES.find(value => value === source.property_class)
  if (identifier(row.request_id) !== id || (source.kind !== 'private' && source.kind !== 'listing') || !propertyClass
    || typeof row.category_id !== 'string' || !CATEGORY.test(row.category_id) || typeof provider.user_code !== 'number'
    || !Number.isSafeInteger(provider.user_code) || provider.user_code < 1) invalid()
  return { request_id: id, category_id: row.category_id,
    source: { kind: source.kind, id: identifier(source.id), title: label(source.title, 4096), property_class: propertyClass },
    provider: { user_code: provider.user_code, member_alias: label(provider.member_alias, 120) } }
}
