import type { AxiosResponse } from 'axios'
import client from './axios'
import { ServiceContractError, ServiceInputError } from './serviceWorkflow'

export const CONVERSATION_MESSAGE_LIMIT = 2000
export const CONVERSATION_PAGE_SIZE = 30
const MAX_PAGE = 1000
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/
const CONTROLS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/

export type ConversationSender = 'owner' | 'provider'

export type ServiceMessage = {
  id: string
  sender: ConversationSender
  body: string
  created_at: string
}

type ConversationPage = {
  request_id: string
  items: ServiceMessage[]
  total: number
  page: number
  page_size: typeof CONVERSATION_PAGE_SIZE
}

export type ConversationSubmission = {
  requestId: string
  sender: ConversationSender
  payload: { submission_id: string; body: string }
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

function sender(value: unknown): ConversationSender {
  return value === 'owner' || value === 'provider' ? value : invalid()
}

function validMessageBody(body: string): boolean {
  return body.length > 0 && [...body].length <= CONVERSATION_MESSAGE_LIMIT && !CONTROLS.test(body)
}

export function conversationBody(value: string): string | null {
  const body = value.trim()
  return validMessageBody(body) ? body : null
}

function message(value: unknown): ServiceMessage {
  const row = exactObject(value, ['id', 'sender', 'body', 'created_at'])
  // Validate stored text without applying the browser's input normalization.
  if (typeof row.body !== 'string' || !validMessageBody(row.body)
    || typeof row.created_at !== 'string' || !TIMESTAMP.test(row.created_at) || !Number.isFinite(Date.parse(row.created_at))) invalid()
  return { id: identifier(row.id), sender: sender(row.sender), body: row.body, created_at: row.created_at }
}

function privateData(response: AxiosResponse<unknown>): unknown {
  const cache = response.headers['cache-control']
  if (typeof cache !== 'string' || !cache.toLowerCase().split(',').some(value => value.trim() === 'no-store')) invalid()
  return response.data
}

const conversationPath = (requestId: string) => `/property-network/service-requests/${identifier(requestId)}/conversation`
const requestOptions = (signal?: AbortSignal) => ({ signal, withCredentials: true, timeout: 15000 })

export async function getServiceConversation(requestId: string, page: number, signal?: AbortSignal): Promise<ConversationPage> {
  const id = identifier(requestId)
  if (!Number.isInteger(page) || page < 1 || page > MAX_PAGE) invalid()
  const response = await client.get(conversationPath(id), { ...requestOptions(signal), params: { page } })
  const row = exactObject(privateData(response), ['request_id', 'items', 'total', 'page', 'page_size'])
  if (identifier(row.request_id) !== id || row.page !== page || row.page_size !== CONVERSATION_PAGE_SIZE
    || !Array.isArray(row.items) || row.items.length > CONVERSATION_PAGE_SIZE || typeof row.total !== 'number'
    || !Number.isSafeInteger(row.total) || row.total < 0) invalid()
  const items = row.items.map(message)
  if (new Set(items.map(item => item.id)).size !== items.length) invalid()
  return { request_id: id, items, total: row.total, page, page_size: CONVERSATION_PAGE_SIZE }
}

export function prepareConversationSubmission(submission: ConversationSubmission) {
  try {
    identifier(submission.requestId)
    sender(submission.sender)
    const row = exactObject(submission.payload, ['submission_id', 'body'])
    if (typeof row.body !== 'string' || conversationBody(row.body) !== row.body) invalid()
    return { submission_id: identifier(row.submission_id), body: row.body }
  } catch (error) {
    if (error instanceof ServiceContractError) throw new ServiceInputError()
    throw error
  }
}

function appliedMessage(response: AxiosResponse<unknown>, submission: ConversationSubmission): ServiceMessage {
  const applied = message(privateData(response))
  if (applied.sender !== submission.sender || applied.body !== submission.payload.body) invalid()
  return applied
}

export async function sendConversationMessage(submission: ConversationSubmission, signal?: AbortSignal) {
  const payload = prepareConversationSubmission(submission)
  const response = await client.post(conversationPath(submission.requestId), payload, requestOptions(signal))
  return appliedMessage(response, submission)
}

export async function resolveConversationMessage(submission: ConversationSubmission, signal?: AbortSignal) {
  const { submission_id } = prepareConversationSubmission(submission)
  const response = await client.get(`${conversationPath(submission.requestId)}/submissions/${submission_id}`, requestOptions(signal))
  return appliedMessage(response, submission)
}
