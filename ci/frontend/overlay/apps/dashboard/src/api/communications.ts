import client from './axios'

export const COMMUNICATION_STATUSES = ['pending', 'processing', 'retry_wait', 'accepted', 'failed', 'needs_review', 'skipped'] as const
export type CommunicationStatus = typeof COMMUNICATION_STATUSES[number]
export type CommunicationEvent = 'lead' | 'signup'
export type CommunicationJob = {
  id: string; event_kind: CommunicationEvent; channel: 'email'; provider: 'resend'; step: string
  status: CommunicationStatus; attempts: number; max_attempts: number; created_at: string
  first_attempt_at: string | null; last_attempt_at: string | null; next_attempt_at: string
  accepted_at: string | null; error_code: string | null; outcome_uncertain: boolean; can_retry: boolean
}
export type CommunicationsReport = {
  total: number; limit: number; offset: number; enabled: boolean; customer_messages_enabled: boolean
  items: CommunicationJob[]
}
export type CommunicationsFilters = { status?: CommunicationStatus; event_kind?: CommunicationEvent; limit?: number; offset?: number }
export type CommunicationRetry = { id: string; status: 'pending'; enabled: boolean }

function record(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value) }
function count(value: unknown): value is number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 }
function id(value: unknown): value is string { return typeof value === 'string' && /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(value) }
function time(value: unknown): value is string { return typeof value === 'string' && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)) }
function nullableTime(value: unknown): value is string | null { return value === null || time(value) }
function status(value: unknown): value is CommunicationStatus { return typeof value === 'string' && (COMMUNICATION_STATUSES as readonly string[]).includes(value) }
function event(value: unknown): value is CommunicationEvent { return value === 'lead' || value === 'signup' }
function step(value: unknown): value is string { return typeof value === 'string' && /^(?:email_(?:[1-9]|10|configuration)|customer_email(?:_configuration)?)$/.test(value) }
function code(value: unknown): value is string | null { return value === null || typeof value === 'string' && /^[a-z\d_]{1,120}$/.test(value) }
function privateExtras(value: Record<string, unknown>): boolean {
  return ['payload', 'payload_sha256', 'recipient', 'recipients', 'email', 'phone', 'message', 'body', 'html', 'text', 'subject', 'lead_id', 'user_id', 'to', 'from'].some(key => key in value)
}

function projectJob(value: unknown): CommunicationJob {
  if (!record(value) || privateExtras(value) || !id(value.id) || !event(value.event_kind)
    || value.channel !== 'email' || value.provider !== 'resend' || !step(value.step) || !status(value.status)
    || !count(value.attempts) || !count(value.max_attempts) || value.max_attempts < 1 || value.max_attempts > 10
    || value.attempts > value.max_attempts || !time(value.created_at) || !time(value.next_attempt_at)
    || !nullableTime(value.first_attempt_at) || !nullableTime(value.last_attempt_at) || !nullableTime(value.accepted_at)
    || !code(value.error_code) || typeof value.outcome_uncertain !== 'boolean' || typeof value.can_retry !== 'boolean'
    || value.status === 'accepted' && (!time(value.accepted_at) || value.attempts < 1 || value.outcome_uncertain || value.can_retry)
    || value.can_retry && (!['failed', 'retry_wait'].includes(value.status) || value.outcome_uncertain || value.attempts < 1 || value.attempts >= value.max_attempts || !value.error_code)) {
    throw new Error('Communications unavailable')
  }
  // Copy only the reviewed operational fields, never arbitrary server extras.
  return { id: value.id, event_kind: value.event_kind, channel: 'email', provider: 'resend', step: value.step,
    status: value.status, attempts: value.attempts, max_attempts: value.max_attempts, created_at: value.created_at,
    first_attempt_at: value.first_attempt_at, last_attempt_at: value.last_attempt_at, next_attempt_at: value.next_attempt_at,
    accepted_at: value.accepted_at, error_code: value.error_code, outcome_uncertain: value.outcome_uncertain, can_retry: value.can_retry }
}

export async function getCommunications(filters: CommunicationsFilters = {}, signal?: AbortSignal): Promise<CommunicationsReport> {
  const params = { limit: filters.limit ?? 25, offset: filters.offset ?? 0,
    ...(filters.status ? { status: filters.status } : {}), ...(filters.event_kind ? { event_kind: filters.event_kind } : {}) }
  if (!Number.isInteger(params.limit) || params.limit < 1 || params.limit > 100
    || !Number.isInteger(params.offset) || params.offset < 0 || params.offset > 10000
    || filters.status !== undefined && !status(filters.status) || filters.event_kind !== undefined && !event(filters.event_kind)) throw new Error('Invalid communications filters')
  const value: unknown = (await client.get('/admin/communications', { params, signal })).data
  if (!record(value) || privateExtras(value) || !count(value.total) || value.limit !== params.limit || value.offset !== params.offset
    || typeof value.enabled !== 'boolean' || typeof value.customer_messages_enabled !== 'boolean' || !Array.isArray(value.items)
    || value.items.length > params.limit) throw new Error('Communications unavailable')
  const items = value.items.map(projectJob)
  if (new Set(items.map(item => item.id)).size !== items.length
    || items.some(item => filters.status && item.status !== filters.status || filters.event_kind && item.event_kind !== filters.event_kind)) throw new Error('Communications unavailable')
  return { total: value.total, limit: params.limit, offset: params.offset, enabled: value.enabled,
    customer_messages_enabled: value.customer_messages_enabled, items }
}

export class CommunicationRetryError extends Error {
  constructor(public readonly code: 'not_retryable' | 'unconfirmed') { super(code) }
}
export async function authorizeCommunicationRetry(jobId: string): Promise<CommunicationRetry> {
  if (!id(jobId)) throw new CommunicationRetryError('unconfirmed')
  try {
    const value: unknown = (await client.post(`/admin/communications/${jobId.toLowerCase()}/retry`, {})).data
    if (!record(value) || privateExtras(value) || value.id !== jobId.toLowerCase() || value.status !== 'pending' || typeof value.enabled !== 'boolean') throw new CommunicationRetryError('unconfirmed')
    return { id: value.id, status: 'pending', enabled: value.enabled }
  } catch (error) {
    if (record(error) && record(error.response) && error.response.status === 409) throw new CommunicationRetryError('not_retryable')
    if (error instanceof CommunicationRetryError) throw error
    // A lost response does not prove the authorization failed. Never retry here.
    throw new CommunicationRetryError('unconfirmed')
  }
}
