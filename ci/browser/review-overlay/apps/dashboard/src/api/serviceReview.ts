import type { AxiosResponse } from 'axios'
import client from './axios'
import { ServiceContractError, ServiceInputError } from './serviceWorkflow'

export const SERVICE_REVIEW_COMMENT_LIMIT = 2000
export const SERVICE_REVIEW_STARS = [1, 2, 3, 4, 5] as const

const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/
const CONTROLS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/
const EDGE_WHITESPACE = /^\p{White_Space}+|\p{White_Space}+$/gu

type ReviewStars = typeof SERVICE_REVIEW_STARS[number]
export type ServiceReview = { id: string; stars: ReviewStars; comment: string | null; created_at: string }
type ServiceReviewState = { request_id: string; review: ServiceReview | null; can_submit: boolean }
export type ServiceReviewSubmission = {
  requestId: string
  payload: { submission_id: string; stars: ReviewStars; comment: string | null }
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

export function isServiceReviewRating(value: unknown): value is ReviewStars {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 5
}

function validStoredComment(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && [...value].length <= SERVICE_REVIEW_COMMENT_LIMIT && !CONTROLS.test(value)
}

export function normalizeServiceReviewComment(value: string): string | null | undefined {
  if (CONTROLS.test(value)) return undefined
  // Match the server's whitespace handling after rejected controls are removed from consideration.
  const comment = value.replace(EDGE_WHITESPACE, '')
  if (!comment) return null
  return validStoredComment(comment) ? comment : undefined
}

function review(value: unknown): ServiceReview {
  const row = exactObject(value, ['id', 'stars', 'comment', 'created_at'])
  if (!isServiceReviewRating(row.stars) || (row.comment !== null && !validStoredComment(row.comment))
    || typeof row.created_at !== 'string' || !TIMESTAMP.test(row.created_at) || !Number.isFinite(Date.parse(row.created_at))) invalid()
  return { id: identifier(row.id), stars: row.stars, comment: row.comment, created_at: row.created_at }
}

function privateData(response: AxiosResponse<unknown>): unknown {
  const cache = response.headers['cache-control']
  if (typeof cache !== 'string' || !cache.toLowerCase().split(',').some(value => value.trim() === 'no-store')) invalid()
  return response.data
}

const reviewPath = (requestId: string) => `/property-network/service-requests/${identifier(requestId)}/review`
const options = (signal?: AbortSignal) => ({ signal, withCredentials: true, timeout: 15000 })

export async function getServiceReview(requestId: string, signal?: AbortSignal): Promise<ServiceReviewState> {
  const id = identifier(requestId)
  const response = await client.get(reviewPath(id), options(signal))
  const row = exactObject(privateData(response), ['request_id', 'review', 'can_submit'])
  if (identifier(row.request_id) !== id || typeof row.can_submit !== 'boolean' || (row.review !== null && row.can_submit)) invalid()
  return { request_id: id, review: row.review === null ? null : review(row.review), can_submit: row.can_submit }
}

export function prepareServiceReviewSubmission(submission: ServiceReviewSubmission) {
  try {
    identifier(submission.requestId)
    const row = exactObject(submission.payload, ['submission_id', 'stars', 'comment'])
    if (!isServiceReviewRating(row.stars) || (row.comment !== null
      && (typeof row.comment !== 'string' || normalizeServiceReviewComment(row.comment) !== row.comment))) invalid()
    return { submission_id: identifier(row.submission_id), stars: row.stars, comment: row.comment }
  } catch (error) {
    if (error instanceof ServiceContractError) throw new ServiceInputError()
    throw error
  }
}

function appliedReview(response: AxiosResponse<unknown>, submission: ServiceReviewSubmission): ServiceReview {
  const saved = review(privateData(response))
  if (saved.stars !== submission.payload.stars || saved.comment !== submission.payload.comment) invalid()
  return saved
}

export async function submitServiceReview(submission: ServiceReviewSubmission, signal?: AbortSignal) {
  const payload = prepareServiceReviewSubmission(submission)
  const response = await client.post(reviewPath(submission.requestId), payload, options(signal))
  return appliedReview(response, submission)
}

export async function resolveServiceReview(submission: ServiceReviewSubmission, signal?: AbortSignal) {
  const { submission_id } = prepareServiceReviewSubmission(submission)
  const response = await client.get(`${reviewPath(submission.requestId)}/submissions/${submission_id}`, options(signal))
  return appliedReview(response, submission)
}
