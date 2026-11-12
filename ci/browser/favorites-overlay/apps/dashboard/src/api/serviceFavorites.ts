import type { AxiosResponse } from 'axios'
import client from './axios'
import { ServiceContractError } from './serviceWorkflow'

const PATH = '/property-network/service-favorites'
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/
const CONTROLS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/
export const SERVICE_FAVORITE_PAGE_SIZE = 30

export type ServiceFavorite = {
  id: string
  provider: { user_code: number; member_alias: string } | null
  saved_at: string
}

function invalid(): never { throw new ServiceContractError() }

function identifier(value: unknown): string {
  return typeof value === 'string' && UUID.test(value) ? value.toLowerCase() : invalid()
}

function integer(value: unknown, minimum: number, maximum = Number.MAX_SAFE_INTEGER): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : invalid()
}

function exactObject(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== fields.length
    || !fields.every(field => Object.prototype.hasOwnProperty.call(value, field))) invalid()
  return value as Record<string, unknown>
}

function favorite(value: unknown): ServiceFavorite {
  const row = exactObject(value, ['id', 'provider', 'saved_at'])
  const savedAt = row.saved_at
  if (typeof savedAt !== 'string' || !TIMESTAMP.test(savedAt) || !Number.isFinite(Date.parse(savedAt))) invalid()
  let provider: ServiceFavorite['provider'] = null
  if (row.provider !== null) {
    const member = exactObject(row.provider, ['user_code', 'member_alias'])
    const alias = member.member_alias
    if (typeof alias !== 'string' || !alias.trim() || [...alias].length > 120 || CONTROLS.test(alias)) invalid()
    provider = { user_code: integer(member.user_code, 1), member_alias: alias }
  }
  return { id: identifier(row.id), provider, saved_at: savedAt }
}

function privateResponse(response: AxiosResponse<unknown>, status: number): unknown {
  const cache = response.headers['cache-control']
  if (response.status !== status || typeof cache !== 'string' || !cache.toLowerCase().split(',').some(value => value.trim() === 'no-store')) invalid()
  return response.data
}

const options = (signal?: AbortSignal) => ({ signal, withCredentials: true, timeout: 15000 })

export async function getServiceFavorites(page: number, signal?: AbortSignal) {
  integer(page, 1, 1000)
  const response = await client.get(PATH, { ...options(signal), params: { page } })
  const row = exactObject(privateResponse(response, 200), ['items', 'total', 'page', 'page_size'])
  if (row.page !== page || row.page_size !== SERVICE_FAVORITE_PAGE_SIZE || !Array.isArray(row.items)
    || row.items.length > SERVICE_FAVORITE_PAGE_SIZE) invalid()
  const items = row.items.map(favorite)
  const codes = items.flatMap(item => item.provider ? [item.provider.user_code] : [])
  if (new Set(items.map(item => item.id)).size !== items.length || new Set(codes).size !== codes.length) invalid()
  return { items, total: integer(row.total, items.length), page, page_size: SERVICE_FAVORITE_PAGE_SIZE }
}

export async function saveServiceFavorite(providerCode: number, signal?: AbortSignal) {
  const code = integer(providerCode, 1)
  const response = await client.post(PATH, { provider_user_code: code }, options(signal))
  const saved = favorite(privateResponse(response, 201))
  if (saved.provider?.user_code !== code) invalid()
  return saved
}

export async function removeServiceFavorite(favoriteId: string, signal?: AbortSignal): Promise<void> {
  const response = await client.delete(`${PATH}/${identifier(favoriteId)}`, {
    ...options(signal), headers: { 'Content-Type': 'application/json' }, data: {},
  })
  const body = privateResponse(response, 204)
  if (body !== '' && body !== null && body !== undefined) invalid()
}
