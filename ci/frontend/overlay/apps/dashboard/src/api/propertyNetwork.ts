import client from './axios'

const PREFIX = '/property-network'
export const NETWORK_PAGE_SIZE = 30
const MAX_PHOTO_BYTES = 5 * 1024 * 1024
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const PROJECT_STATUSES = ['open', 'in_progress', 'completed', 'archived'] as const
const MILESTONE_STATUSES = ['open', 'in_progress', 'completed', 'cancelled'] as const
const UPDATE_KINDS = ['progress', 'correction', 'milestone'] as const
const MEMBER_ROLES = ['owner', 'manager', 'professional', 'viewer'] as const
export const PRIVATE_PROPERTY_CLASSES = ['house_villa', 'apartment_condominium', 'multiunit_building', 'vacation_rental', 'hotel_hospitality', 'office', 'retail_restaurant', 'warehouse_industrial', 'farm_rural', 'vacant_land'] as const
const PROPERTY_RELATIONSHIPS = ['owner', 'manager', 'seller'] as const
const PROPERTY_STATUSES = ['active', 'archived'] as const
export type ProjectStatus = typeof PROJECT_STATUSES[number]
export type MilestoneStatus = typeof MILESTONE_STATUSES[number]
export type ProjectRole = typeof MEMBER_ROLES[number] | 'admin'
export type AssignedProjectRole = Exclude<typeof MEMBER_ROLES[number], 'owner'>
export type PageParams = { page?: number }
export type NetworkPage<T> = { items: T[]; total: number; page: number; page_size: number }
export type NetworkAuthor = { user_code: number | null; display_name: string }
export type NetworkProperty = { id: string; title: string; location: string; type: string }
export type NetworkProject = {
  id: string; listing_id: string | null; listing_title: string; property_id: string | null; property_title: string | null; title: string; summary: string
  property_class: string; service_category_ids: string[]; status: ProjectStatus; version: number
  my_role: ProjectRole; can_manage: boolean; can_assign: boolean; can_update: boolean
  progress: { total: number; completed: number; percent: number | null }; created_at: string; updated_at: string
}
export type ProjectMember = NetworkAuthor & { role: typeof MEMBER_ROLES[number]; active: boolean }
export type NetworkMilestone = { id: string; project_id: string; title: string; due_date: string | null; status: MilestoneStatus; version: number }
export type NetworkUpdate = {
  id: string; project_id: string; kind: typeof UPDATE_KINDS[number]; body: string; occurred_on: string
  correction_of: string | null; author: NetworkAuthor; media_ids: string[]; created_at: string
}
export type NetworkComment = { id: string; body: string; media_id: string | null; author: NetworkAuthor; hidden: boolean; can_hide: boolean; created_at: string }
export type NetworkProfile = {
  user_code: number; display_name: string; bio: string; status_text: string; visibility: 'private' | 'members'
  wall_policy: 'own_only' | 'approval_required'; comments_enabled: boolean; version: number; can_edit: boolean
}
export type NetworkPost = {
  id: string; profile_user_code: number; author: NetworkAuthor; body: string; media_ids: string[]
  status: 'pending' | 'visible' | 'hidden'; can_moderate: boolean; can_comment: boolean; created_at: string
}
export type ServiceHistoryItem = {
  id: string; project_id: string; project_title: string; service_category_ids: string[]; project_status: ProjectStatus
  kind: typeof UPDATE_KINDS[number]; body: string; occurred_on: string; media_ids: string[]; created_at: string
}
export type NetworkMedia = { id: string; url: string; content_type: 'image/webp'; bytes: number; sha256: string; width: number; height: number }
export type CreateProjectInput = { listing_id?: string | null; property_id?: string | null; submission_id?: string; title: string; summary: string; property_class: string; service_category_ids: string[] }
export type PrivatePropertyStatus = typeof PROPERTY_STATUSES[number]
export type PrivatePropertyFields = { title: string; location: string; address: string; property_class: typeof PRIVATE_PROPERTY_CLASSES[number]; relationship: typeof PROPERTY_RELATIONSHIPS[number]; notes: string }
export type PrivateProperty = PrivatePropertyFields & { id: string; status: PrivatePropertyStatus; version: number; created_at: string; updated_at: string; can_manage: boolean; can_create_project: boolean }
export type PrivatePropertyPage = NetworkPage<PrivateProperty> & { can_create: boolean }
export type CreatePrivatePropertyInput = PrivatePropertyFields & { submission_id: string; authority_acknowledged: boolean }
export type UpdatePrivatePropertyInput = PrivatePropertyFields & { expected_version: number; status: PrivatePropertyStatus }
export type UpdateProjectInput = { expected_version: number; title: string; summary: string; status: ProjectStatus }
export type AddProjectMemberInput = { expected_version: number; user_code: number; role: AssignedProjectRole }
export type CreateMilestoneInput = { title: string; due_date: string | null }
export type UpdateMilestoneInput = CreateMilestoneInput & { expected_version: number; status: MilestoneStatus }
export type CreateProjectUpdateInput = { submission_id: string; body: string; occurred_on: string; correction_of?: string | null; media_ids: string[] }
export type CreateNetworkCommentInput = { submission_id: string; body: string; media_id?: string | null }
export type SaveNetworkProfileInput = {
  expected_version: number; display_name: string; bio: string; status_text: string; visibility: 'private' | 'members'
  wall_policy: 'own_only' | 'approval_required'; comments_enabled: boolean
}
export type CreateProfilePostInput = { submission_id: string; body: string; media_ids: string[] }

export class PropertyNetworkContractError extends Error {
  constructor() { super('Invalid property network data. Reload and try again.'); this.name = 'PropertyNetworkContractError' }
}
function invalid(): never { throw new PropertyNetworkContractError() }
function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid()
}
function text(value: unknown, maximum: number, minimum = 0): string {
  if (typeof value !== 'string' || [...value].length > maximum || [...value].length < minimum || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)) invalid()
  return value
}
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : invalid()
}
function bool(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid() }
function uuid(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function nullableUUID(value: unknown): string | null { return value === null ? null : uuid(value) }
function choice<T extends string>(value: unknown, options: readonly T[]): T { return typeof value === 'string' && options.includes(value as T) ? value as T : invalid() }
function slug(value: unknown): string { const result = text(value, 80, 1); return /^[a-z][a-z0-9_]*$/.test(result) ? result : invalid() }
function array<T>(value: unknown, maximum: number, decode: (item: unknown) => T): T[] {
  return Array.isArray(value) && value.length <= maximum ? value.map(decode) : invalid()
}
function uniqueStrings(value: unknown, maximum: number, decode: (item: unknown) => string): string[] {
  const result = array(value, maximum, decode)
  return new Set(result.map(item => item.toLowerCase())).size === result.length ? result : invalid()
}
function day(value: unknown): string {
  const result = text(value, 10, 10)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(result)) invalid()
  const parsed = new Date(`${result}T00:00:00Z`)
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === result ? result : invalid()
}
function nullableDay(value: unknown): string | null { return value === null ? null : day(value) }
function timestamp(value: unknown): string {
  const result = text(value, 40, 20)
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(result) || !Number.isFinite(Date.parse(result))) invalid()
  day(result.slice(0, 10)); return result
}
function currentDayDR(): string {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Santo_Domingo', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
  const part = (kind: string) => parts.find(value => value.type === kind)?.value ?? invalid()
  return `${part('year')}-${part('month')}-${part('day')}`
}
function author(value: unknown): NetworkAuthor {
  const row = object(value)
  return { user_code: row.user_code === null ? null : integer(row.user_code, 1), display_name: text(row.display_name, 100) }
}
function property(value: unknown): NetworkProperty {
  const row = object(value)
  return { id: uuid(row.id), title: text(row.title, 4096), location: text(row.location, 2048), type: choice(row.type, ['villa', 'apartment', 'condo', 'land', 'commercial']) }
}
function project(value: unknown): NetworkProject {
  const row = object(value), rawProgress = object(row.progress)
  const listingId = nullableUUID(row.listing_id), propertyId = row.property_id === undefined ? null : nullableUUID(row.property_id)
  const propertyTitle = row.property_title === undefined || row.property_title === null ? null : text(row.property_title, 180, 1)
  if ((listingId === null) === (propertyId === null) || (propertyId === null) !== (propertyTitle === null)) invalid()
  const total = integer(rawProgress.total), completed = integer(rawProgress.completed, 0, total)
  const percent = rawProgress.percent
  if (total === 0 ? percent !== null : typeof percent !== 'number' || !Number.isFinite(percent) || percent < 0 || percent > 100) invalid()
  return {
    id: uuid(row.id), listing_id: listingId, listing_title: text(row.listing_title, 4096), property_id: propertyId, property_title: propertyTitle, title: text(row.title, 180, 1), summary: text(row.summary, 4000),
    property_class: slug(row.property_class), service_category_ids: uniqueStrings(row.service_category_ids, 12, slug), status: choice(row.status, PROJECT_STATUSES), version: integer(row.version, 1),
    my_role: choice(row.my_role, [...MEMBER_ROLES, 'admin']), can_manage: bool(row.can_manage), can_assign: bool(row.can_assign), can_update: bool(row.can_update),
    progress: { total, completed, percent: percent as number | null }, created_at: timestamp(row.created_at), updated_at: timestamp(row.updated_at),
  }
}
function privatePropertyFields(value: unknown): PrivatePropertyFields {
  const row = object(value)
  return { title: text(text(row.title, 180).trim(), 180, 1), location: text(text(row.location, 180).trim(), 180, 1),
    address: text(row.address, 500), property_class: choice(row.property_class, PRIVATE_PROPERTY_CLASSES),
    relationship: choice(row.relationship, PROPERTY_RELATIONSHIPS), notes: text(row.notes, 4000) }
}
function privateProperty(value: unknown): PrivateProperty {
  const row = object(value), status = choice(row.status, PROPERTY_STATUSES), canManage = bool(row.can_manage), canCreate = bool(row.can_create_project)
  if (canCreate && (!canManage || status !== 'active')) invalid()
  return { ...privatePropertyFields(row), id: uuid(row.id), status, version: integer(row.version, 1),
    created_at: timestamp(row.created_at), updated_at: timestamp(row.updated_at), can_manage: canManage, can_create_project: canCreate }
}
function member(value: unknown): ProjectMember {
  const row = object(value); return { ...author(row), role: choice(row.role, MEMBER_ROLES), active: bool(row.active) }
}
function milestone(value: unknown): NetworkMilestone {
  const row = object(value)
  return { id: uuid(row.id), project_id: uuid(row.project_id), title: text(row.title, 180, 1), due_date: nullableDay(row.due_date), status: choice(row.status, MILESTONE_STATUSES), version: integer(row.version, 1) }
}
function update(value: unknown): NetworkUpdate {
  const row = object(value)
  return { id: uuid(row.id), project_id: uuid(row.project_id), kind: choice(row.kind, UPDATE_KINDS), body: text(row.body, 4000, 1), occurred_on: day(row.occurred_on), correction_of: nullableUUID(row.correction_of), author: author(row.author), media_ids: uniqueStrings(row.media_ids, 6, uuid), created_at: timestamp(row.created_at) }
}
function comment(value: unknown): NetworkComment {
  const row = object(value)
  return { id: uuid(row.id), body: text(row.body, 2000, 1), media_id: nullableUUID(row.media_id), author: author(row.author), hidden: bool(row.hidden), can_hide: bool(row.can_hide), created_at: timestamp(row.created_at) }
}
function profile(value: unknown): NetworkProfile {
  const row = object(value)
  return { user_code: integer(row.user_code, 1), display_name: text(row.display_name, 100), bio: text(row.bio, 2000), status_text: text(row.status_text, 280), visibility: choice(row.visibility, ['private', 'members']), wall_policy: choice(row.wall_policy, ['own_only', 'approval_required']), comments_enabled: bool(row.comments_enabled), version: integer(row.version), can_edit: bool(row.can_edit) }
}
function post(value: unknown): NetworkPost {
  const row = object(value)
  return { id: uuid(row.id), profile_user_code: integer(row.profile_user_code, 1), author: author(row.author), body: text(row.body, 4000, 1), media_ids: uniqueStrings(row.media_ids, 6, uuid), status: choice(row.status, ['pending', 'visible', 'hidden']), can_moderate: bool(row.can_moderate), can_comment: bool(row.can_comment), created_at: timestamp(row.created_at) }
}
function history(value: unknown): ServiceHistoryItem {
  const row = object(value)
  return { id: uuid(row.id), project_id: uuid(row.project_id), project_title: text(row.project_title, 180, 1), service_category_ids: uniqueStrings(row.service_category_ids, 12, slug), project_status: choice(row.project_status, PROJECT_STATUSES), kind: choice(row.kind, UPDATE_KINDS), body: text(row.body, 4000, 1), occurred_on: day(row.occurred_on), media_ids: uniqueStrings(row.media_ids, 6, uuid), created_at: timestamp(row.created_at) }
}
function media(value: unknown): NetworkMedia {
  const row = object(value), id = uuid(row.id)
  if (row.url !== `${PREFIX}/media/${id}` || typeof row.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(row.sha256)) invalid()
  return { id, url: `${PREFIX}/media/${id}`, content_type: choice(row.content_type, ['image/webp']), bytes: integer(row.bytes, 1, MAX_PHOTO_BYTES), sha256: row.sha256, width: integer(row.width, 1, 1800), height: integer(row.height, 1, 1800) }
}
function sameId<T extends { id: string }>(row: T, id: string): T { return row.id.toLowerCase() === id.toLowerCase() ? row : invalid() }
function sameProject<T extends { project_id: string }>(row: T, id: string): T { return row.project_id.toLowerCase() === id.toLowerCase() ? row : invalid() }
function pageNumber(params: PageParams): number { return params.page === undefined ? 1 : integer(params.page, 1, 1000) }
async function readPage<T>(path: string, params: PageParams, decode: (row: unknown) => T, identity: (row: T) => string | number, signal?: AbortSignal, extra: Record<string, string> = {}): Promise<NetworkPage<T>> {
  const expectedPage = pageNumber(params)
  const row = object((await client.get<unknown>(path, { params: { page: expectedPage, ...extra }, signal, withCredentials: true })).data)
  const items = array(row.items, NETWORK_PAGE_SIZE, decode), total = integer(row.total)
  if (integer(row.page, 1, 1000) !== expectedPage || integer(row.page_size, NETWORK_PAGE_SIZE, NETWORK_PAGE_SIZE) !== NETWORK_PAGE_SIZE || total < items.length || new Set(items.map(identity)).size !== items.length) invalid()
  return { items, total, page: expectedPage, page_size: NETWORK_PAGE_SIZE }
}
function submission(input: { submission_id: string; body: string }, maximum: number) { return { submission_id: uuid(input.submission_id), body: text(input.body, maximum, 1) } }
function commentSubmission(input: CreateNetworkCommentInput) { return { ...submission(input, 2000), ...(input.media_id === undefined ? {} : { media_id: nullableUUID(input.media_id) }) } }
function milestoneInput(input: CreateMilestoneInput) { return { title: text(input.title, 180, 1), due_date: nullableDay(input.due_date) } }
async function membersResult(raw: unknown): Promise<ProjectMember[]> {
  const items = array(raw, 101, member), codes = items.filter(item => item.user_code !== null).map(item => item.user_code)
  if (new Set(codes).size !== codes.length) invalid()
  return items
}

export function getNetworkProperties(params: PageParams = {}, signal?: AbortSignal): Promise<NetworkPage<NetworkProperty>> { return readPage(`${PREFIX}/properties`, params, property, row => row.id, signal) }
export function getProjects(params: PageParams & { listing_id?: string; property_id?: string } = {}, signal?: AbortSignal): Promise<NetworkPage<NetworkProject>> {
  if (params.listing_id !== undefined && params.property_id !== undefined) invalid()
  return readPage(`${PREFIX}/projects`, params, raw => {
    const row = project(raw)
    if (params.property_id !== undefined && row.property_id?.toLowerCase() !== params.property_id.toLowerCase() || params.listing_id !== undefined && row.listing_id?.toLowerCase() !== params.listing_id.toLowerCase()) invalid()
    return row
  }, row => row.id, signal, params.property_id !== undefined ? { property_id: uuid(params.property_id) } : params.listing_id !== undefined ? { listing_id: uuid(params.listing_id) } : {})
}
export async function getProject(id: string, signal?: AbortSignal): Promise<NetworkProject> { uuid(id); return sameId(project((await client.get(`${PREFIX}/projects/${id}`, { signal, withCredentials: true })).data), id) }
export async function getProjectSubmission(submissionId: string, signal?: AbortSignal): Promise<NetworkProject> { uuid(submissionId); return project((await client.get(`${PREFIX}/projects/submissions/${submissionId}`, { signal, withCredentials: true })).data) }
export async function createProject(input: CreateProjectInput, signal?: AbortSignal): Promise<NetworkProject> {
  const listingId = input.listing_id == null ? null : uuid(input.listing_id), propertyId = input.property_id == null ? null : uuid(input.property_id)
  if ((listingId === null) === (propertyId === null) || (propertyId !== null && input.submission_id === undefined)) invalid()
  const body = { ...(listingId === null ? { property_id: propertyId! } : { listing_id: listingId }),
    ...(input.submission_id === undefined ? {} : { submission_id: uuid(input.submission_id) }),
    title: text(input.title, 180, 1), summary: text(input.summary, 4000), property_class: slug(input.property_class), service_category_ids: uniqueStrings(input.service_category_ids, 12, slug) }
  const result = project((await client.post(`${PREFIX}/projects`, body, { signal, withCredentials: true })).data)
  return result.listing_id?.toLowerCase() === listingId?.toLowerCase() && result.property_id?.toLowerCase() === propertyId?.toLowerCase() && result.property_class === body.property_class ? result : invalid()
}
export async function getPrivateProperties(params: PageParams & { status?: PrivatePropertyStatus | 'all' } = {}, signal?: AbortSignal): Promise<PrivatePropertyPage> {
  const expectedPage = pageNumber(params), status = params.status === undefined ? 'all' : choice(params.status, ['all', ...PROPERTY_STATUSES])
  const row = object((await client.get(`${PREFIX}/registry`, { params: { page: expectedPage, status }, signal, withCredentials: true })).data)
  const items = array(row.items, NETWORK_PAGE_SIZE, privateProperty), total = integer(row.total)
  if (integer(row.page, 1, 1000) !== expectedPage || integer(row.page_size, NETWORK_PAGE_SIZE, NETWORK_PAGE_SIZE) !== NETWORK_PAGE_SIZE || total < items.length || new Set(items.map(item => item.id.toLowerCase())).size !== items.length || status !== 'all' && items.some(item => item.status !== status)) invalid()
  return { items, total, page: expectedPage, page_size: NETWORK_PAGE_SIZE, can_create: bool(row.can_create) }
}
export async function getPrivateProperty(id: string, signal?: AbortSignal): Promise<PrivateProperty> { uuid(id); return sameId(privateProperty((await client.get(`${PREFIX}/registry/${id}`, { signal, withCredentials: true })).data), id) }
export async function getPrivatePropertySubmission(submissionId: string, signal?: AbortSignal): Promise<PrivateProperty> { uuid(submissionId); return privateProperty((await client.get(`${PREFIX}/registry/submissions/${submissionId}`, { signal, withCredentials: true })).data) }
export async function createPrivateProperty(input: CreatePrivatePropertyInput, signal?: AbortSignal): Promise<PrivateProperty> {
  if (input.authority_acknowledged !== true) invalid()
  const body = { ...privatePropertyFields(input), submission_id: uuid(input.submission_id), authority_acknowledged: true }
  return privateProperty((await client.post(`${PREFIX}/registry`, body, { signal, withCredentials: true })).data)
}
export async function updatePrivateProperty(id: string, input: UpdatePrivatePropertyInput, signal?: AbortSignal): Promise<PrivateProperty> {
  uuid(id); const body = { ...privatePropertyFields(input), expected_version: integer(input.expected_version, 1), status: choice(input.status, PROPERTY_STATUSES) }
  return sameId(privateProperty((await client.put(`${PREFIX}/registry/${id}`, body, { signal, withCredentials: true })).data), id)
}
export function getPrivatePropertyServiceHistory(id: string, params: PageParams = {}, signal?: AbortSignal): Promise<NetworkPage<ServiceHistoryItem>> { uuid(id); return readPage(`${PREFIX}/registry/${id}/history`, params, history, row => row.id, signal) }
export async function updateProject(id: string, input: UpdateProjectInput): Promise<NetworkProject> {
  uuid(id); const body = { expected_version: integer(input.expected_version, 1), title: text(input.title, 180, 1), summary: text(input.summary, 4000), status: choice(input.status, PROJECT_STATUSES) }
  return sameId(project((await client.put(`${PREFIX}/projects/${id}`, body, { withCredentials: true })).data), id)
}
export async function getProjectMembers(id: string, signal?: AbortSignal): Promise<ProjectMember[]> { uuid(id); return membersResult((await client.get(`${PREFIX}/projects/${id}/members`, { signal, withCredentials: true })).data) }
export async function addProjectMember(id: string, input: AddProjectMemberInput): Promise<ProjectMember[]> {
  uuid(id); const body = { expected_version: integer(input.expected_version, 1), user_code: integer(input.user_code, 1), role: choice(input.role, ['manager', 'professional', 'viewer']) }
  return membersResult((await client.post(`${PREFIX}/projects/${id}/members`, body, { withCredentials: true })).data)
}
export async function revokeProjectMember(id: string, userCode: number, expectedVersion: number): Promise<ProjectMember[]> {
  uuid(id); integer(userCode, 1)
  return membersResult((await client.post(`${PREFIX}/projects/${id}/members/${userCode}/revoke`, { expected_version: integer(expectedVersion, 1) }, { withCredentials: true })).data)
}
export function getMilestones(id: string, params: PageParams = {}, signal?: AbortSignal): Promise<NetworkPage<NetworkMilestone>> { uuid(id); return readPage(`${PREFIX}/projects/${id}/milestones`, params, row => sameProject(milestone(row), id), row => row.id, signal) }
export async function createMilestone(id: string, input: CreateMilestoneInput): Promise<NetworkMilestone> { uuid(id); return sameProject(milestone((await client.post(`${PREFIX}/projects/${id}/milestones`, milestoneInput(input), { withCredentials: true })).data), id) }
export async function updateMilestone(id: string, input: UpdateMilestoneInput): Promise<NetworkMilestone> {
  uuid(id); return sameId(milestone((await client.put(`${PREFIX}/milestones/${id}`, { ...milestoneInput(input), expected_version: integer(input.expected_version, 1), status: choice(input.status, MILESTONE_STATUSES) }, { withCredentials: true })).data), id)
}
export function getProjectUpdates(id: string, params: PageParams = {}, signal?: AbortSignal): Promise<NetworkPage<NetworkUpdate>> { uuid(id); return readPage(`${PREFIX}/projects/${id}/updates`, params, row => sameProject(update(row), id), row => row.id, signal) }
export async function createProjectUpdate(id: string, input: CreateProjectUpdateInput): Promise<NetworkUpdate> {
  uuid(id); const occurredOn = day(input.occurred_on); if (occurredOn > currentDayDR()) invalid()
  const body = { ...submission(input, 4000), occurred_on: occurredOn, correction_of: input.correction_of === undefined ? null : nullableUUID(input.correction_of), media_ids: uniqueStrings(input.media_ids, 6, uuid) }
  return sameProject(update((await client.post(`${PREFIX}/projects/${id}/updates`, body, { withCredentials: true })).data), id)
}
export function getUpdateComments(id: string, params: PageParams = {}, signal?: AbortSignal): Promise<NetworkPage<NetworkComment>> { uuid(id); return readPage(`${PREFIX}/updates/${id}/comments`, params, comment, row => row.id, signal) }
export async function createUpdateComment(id: string, input: CreateNetworkCommentInput): Promise<NetworkComment> { uuid(id); return comment((await client.post(`${PREFIX}/updates/${id}/comments`, commentSubmission(input), { withCredentials: true })).data) }
export function getPropertyServiceHistory(id: string, params: PageParams = {}, signal?: AbortSignal): Promise<NetworkPage<ServiceHistoryItem>> { uuid(id); return readPage(`${PREFIX}/properties/${id}/history`, params, history, row => row.id, signal) }
export async function getMyNetworkProfile(signal?: AbortSignal): Promise<NetworkProfile> { return profile((await client.get(`${PREFIX}/me/profile`, { signal, withCredentials: true })).data) }
export async function saveNetworkProfile(input: SaveNetworkProfileInput): Promise<NetworkProfile> {
  const body = { expected_version: integer(input.expected_version), display_name: text(input.display_name, 100, 1), bio: text(input.bio, 2000), status_text: text(input.status_text, 280), visibility: choice(input.visibility, ['private', 'members']), wall_policy: choice(input.wall_policy, ['own_only', 'approval_required']), comments_enabled: bool(input.comments_enabled) }
  return profile((await client.put(`${PREFIX}/me/profile`, body, { withCredentials: true })).data)
}
export function getNetworkProfiles(params: PageParams & { q?: string } = {}, signal?: AbortSignal): Promise<NetworkPage<NetworkProfile>> { return readPage(`${PREFIX}/profiles`, params, profile, row => row.user_code, signal, params.q === undefined ? {} : { q: text(params.q, 100) }) }
export async function getNetworkProfile(userCode: number, signal?: AbortSignal): Promise<NetworkProfile> {
  integer(userCode, 1); const row = profile((await client.get(`${PREFIX}/profiles/${userCode}`, { signal, withCredentials: true })).data)
  return row.user_code === userCode ? row : invalid()
}
export function getProfilePosts(userCode: number, params: PageParams = {}, signal?: AbortSignal): Promise<NetworkPage<NetworkPost>> {
  integer(userCode, 1); return readPage(`${PREFIX}/profiles/${userCode}/posts`, params, raw => { const row = post(raw); return row.profile_user_code === userCode ? row : invalid() }, row => row.id, signal)
}
export async function createProfilePost(userCode: number, input: CreateProfilePostInput): Promise<NetworkPost> {
  integer(userCode, 1); const row = post((await client.post(`${PREFIX}/profiles/${userCode}/posts`, { ...submission(input, 4000), media_ids: uniqueStrings(input.media_ids, 6, uuid) }, { withCredentials: true })).data)
  return row.profile_user_code === userCode ? row : invalid()
}
export function getPostComments(id: string, params: PageParams = {}, signal?: AbortSignal): Promise<NetworkPage<NetworkComment>> { uuid(id); return readPage(`${PREFIX}/posts/${id}/comments`, params, comment, row => row.id, signal) }
export async function createPostComment(id: string, input: CreateNetworkCommentInput): Promise<NetworkComment> { uuid(id); return comment((await client.post(`${PREFIX}/posts/${id}/comments`, commentSubmission(input), { withCredentials: true })).data) }
export async function moderatePost(id: string, action: 'approve' | 'hide'): Promise<NetworkPost> { uuid(id); return sameId(post((await client.post(`${PREFIX}/posts/${id}/moderate`, { action: choice(action, ['approve', 'hide']) }, { withCredentials: true })).data), id) }
export async function hideNetworkComment(id: string): Promise<NetworkComment> { uuid(id); return sameId(comment((await client.post(`${PREFIX}/comments/${id}/hide`, {}, { withCredentials: true })).data), id) }
export async function setNetworkBlock(userCode: number, blocked: boolean): Promise<{ blocked: boolean }> {
  integer(userCode, 1); bool(blocked); const row = object((await client.put(`${PREFIX}/me/blocks/${userCode}`, { blocked }, { withCredentials: true })).data)
  return bool(row.blocked) === blocked ? { blocked } : invalid()
}
export async function uploadNetworkMedia(file: File, projectId?: string, signal?: AbortSignal): Promise<NetworkMedia> {
  if (!(file instanceof Blob) || file.size < 1 || file.size > MAX_PHOTO_BYTES || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) invalid()
  const body = new FormData(); body.append('file', file)
  if (projectId !== undefined) body.append('project_id', uuid(projectId))
  return media((await client.post(`${PREFIX}/media`, body, { signal, withCredentials: true })).data)
}
export async function getNetworkMediaBlob(id: string, signal?: AbortSignal): Promise<Blob> {
  uuid(id)
  const response = await client.get(`${PREFIX}/media/${id}`, { responseType: 'blob', signal, withCredentials: true })
  const result: unknown = response.data
  if (!(result instanceof Blob) || result.type !== 'image/webp' || result.size < 1 || result.size > MAX_PHOTO_BYTES || typeof response.headers?.['cache-control'] !== 'string' || !response.headers['cache-control'].toLowerCase().includes('no-store')) invalid()
  return result
}
