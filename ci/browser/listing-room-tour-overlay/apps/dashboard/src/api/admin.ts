import client from './axios'

export interface AdminUpgradeRequest {
  id: string
  user_id: string
  user_email: string
  user_display_name: string
  requested_role: 'owner' | 'realtor'
  status: 'pending' | 'approved' | 'rejected'
  rejection_reason: string | null
  created_at: string
  reviewed_by_name: string | null
  reviewed_at: string | null
  license_number: string | null
  territory: string | null
  years_experience: number | null
  specialties: string | null
  bio: string | null
}

export async function getAdminUpgradeRequests(status?: string): Promise<AdminUpgradeRequest[]> {
  const res = await client.get<AdminUpgradeRequest[]>('/admin/upgrade-requests', {
    params: status ? { status } : undefined,
  })
  return res.data
}

export async function approveUpgradeRequest(id: string): Promise<void> {
  await client.post(`/admin/upgrade-requests/${id}/approve`)
}

export async function rejectUpgradeRequest(id: string, reason: string): Promise<void> {
  await client.post(`/admin/upgrade-requests/${id}/reject`, { reason })
}

export interface AdminListing {
  id: string
  title: string
  description: string | null
  description_es: string | null
  type: string
  transaction: string
  price: number
  location: string
  bedrooms: number | null
  bathrooms: number | null
  area_sqft: number | null
  lot_size_sqft: number | null
  roi: number | null
  seller_financing: boolean
  hoa: boolean
  hoa_fee: number | null
  tax_exempt: boolean
  gated_community: boolean
  construction_status: string | null
  year_built: number | null
  features: string[]
  maps_url: string | null
  latitude: number | null
  longitude: number | null
  tag: string | null
  tags: string[]
  video_links: string[]
  tour_3d_url: string | null
  utilities: string | null
  included_utilities: string[]
  association_fee: number | null
  deposit_policy: string | null
  images: string[]
  status: string
  submitted_by: string
  submitted_by_name: string | null
  submitted_by_email: string | null
  owner_id: string | null
  assigned_realtor_id: string | null
  assigned_realtor_name: string | null
  assigned_realtor_email: string | null
  reviewed_by_name: string | null
  reviewed_by_email: string | null
  reviewed_at: string | null
  view_count: number
  updated_at: string | null
  is_deal: boolean
  deal_discount_value: number | null
  deal_discount_type: string
  co_listing_enabled: boolean
  co_listing_brokerage: string | null
  co_listing_agent_name: string | null
  co_listing_agent_contact: string | null
  co_listing_agent_email: string | null
  co_listing_brokerage_email: string | null
  co_listing_brokerage_phone: string | null
  co_listing_commission_split: number | null
  co_listing_notes: string | null
  co_listing_status: string | null
  currency: string
  source_ref: string | null
  price_per_day: number | null
  price_per_month: number | null
  owner_paypal_email: string | null
  co_listing_agreement_accepted: boolean
  co_listing_agreement_url: string | null
}

export interface AdminListingsQuery {
  page?: number
  pageSize?: number
  status?: string
  excludeStatus?: string
  coListingEnabled?: boolean
  isDeal?: boolean
  q?: string
}

export interface AdminListingsPage {
  items: AdminListing[]
  total: number
  page: number
  page_size: number
}

export async function getAdminListings(query: AdminListingsQuery = {}): Promise<AdminListingsPage> {
  const res = await client.get<AdminListingsPage>('/admin/listings', {
    params: {
      page: query.page,
      page_size: query.pageSize,
      status: query.status,
      exclude_status: query.excludeStatus,
      co_listing_enabled: query.coListingEnabled,
      is_deal: query.isDeal,
      q: query.q,
    },
  })
  return res.data
}

export async function getAdminListing(id: string): Promise<AdminListing> {
  const res = await client.get<AdminListing>(`/admin/listings/${id}`)
  return res.data
}

export async function approveAdminListing(id: string): Promise<void> {
  await client.post(`/admin/listings/${id}/approve`)
}

export async function rejectAdminListing(id: string, reason: string): Promise<void> {
  await client.post(`/admin/listings/${id}/reject`, { reason })
}

export async function archiveAdminListing(id: string): Promise<void> {
  await client.post(`/admin/listings/${id}/archive`)
}

export async function assignAdminListing(id: string, realtorId: string | null): Promise<void> {
  await client.put(`/admin/listings/${id}/assign`, { realtor_id: realtorId })
}

export interface AdminListingEdit {
  id: string
  listing_id: string
  listing_title: string
  listing_location: string
  listing_thumbnail: string | null
  submitted_by_name: string | null
  submitted_by_email: string | null
  submitted_at: string
  status: string
  current_data: Record<string, unknown>
  proposed_data: Record<string, unknown>
  rejection_reason: string | null
}

export async function getAdminListingEdits(): Promise<AdminListingEdit[]> {
  const res = await client.get<AdminListingEdit[]>('/admin/listing-edits')
  return res.data
}

export async function approveListingEdit(id: string): Promise<void> {
  await client.post(`/admin/listing-edits/${id}/approve`)
}

export async function approveListingRoomPhotoTour(id: string): Promise<void> {
  await client.post(`/admin/listing-edits/${id}/approve`, { room_photo_tour_reviewed: true })
}

export async function rejectListingEdit(id: string, reason: string): Promise<void> {
  await client.post(`/admin/listing-edits/${id}/reject`, { reason })
}

export interface AdminStats {
  active_listings: number
  pending_listings: number
  archived_listings: number
  rejected_listings: number
  total_users: number
}

export async function getAdminStats(): Promise<AdminStats> {
  const res = await client.get<AdminStats>('/admin/stats')
  return res.data
}

export interface ActivityEntry {
  id: string
  event_type: string
  description: string
  created_at: string
}

export async function getAdminActivityLog(limit = 20): Promise<ActivityEntry[]> {
  const res = await client.get<ActivityEntry[]>('/admin/activity-log', { params: { limit } })
  return res.data
}

export interface AdminUser {
  id: string
  user_code: number | null
  email: string
  role: string
  status: string
  display_name: string | null
  phone: string | null
  created_at: string
  avatar_url: string | null
  assigned_realtor_id: string | null
  assigned_realtor_name: string | null
}

export async function getAdminUsers(role?: string, status?: string): Promise<AdminUser[]> {
  const res = await client.get<AdminUser[]>('/admin/users', {
    params: { ...(role ? { role } : {}), ...(status ? { status } : {}) },
  })
  return res.data
}

export interface AdminAgent {
  id: string
  display_name: string | null
  email: string
  role: 'admin' | 'realtor'
}

/** Assignment choices only. User management keeps its strict role filters. */
export async function getAdminAgents(): Promise<AdminAgent[]> {
  return (await client.get<AdminAgent[]>('/admin/agents')).data
}

export async function suspendUser(id: string): Promise<void> {
  await client.put(`/admin/users/${id}/suspend`)
}

export async function unsuspendUser(id: string): Promise<void> {
  await client.put(`/admin/users/${id}/unsuspend`)
}

export async function createAdminUser(data: {
  display_name: string
  email: string
  password: string
  role: string
}): Promise<AdminUser> {
  const res = await client.post<AdminUser>('/admin/users', data)
  return res.data
}

export interface DealRequest {
  id: string
  listing_id: string
  listing_title: string
  listing_location: string
  listing_thumbnail: string | null
  requested_by_name: string | null
  requested_by_email: string
  discount_value: number
  discount_type: string
  message: string | null
  status: 'pending' | 'approved' | 'rejected'
  rejection_reason: string | null
  reviewed_by_name: string | null
  reviewed_at: string | null
  created_at: string
}

export async function getAdminDealRequests(status?: string): Promise<DealRequest[]> {
  const res = await client.get<DealRequest[]>('/admin/deal-requests', {
    params: status ? { status } : undefined,
  })
  return res.data
}

export async function approveDealRequest(id: string): Promise<void> {
  await client.post(`/admin/deal-requests/${id}/approve`)
}

export async function rejectDealRequest(id: string, reason: string): Promise<void> {
  await client.post(`/admin/deal-requests/${id}/reject`, { reason })
}

export async function clearListingDeal(id: string): Promise<void> {
  await client.post(`/admin/listings/${id}/clear-deal`)
}

export async function setListingDeal(id: string, discountValue: number | null, discountType: string): Promise<void> {
  await client.post(`/admin/listings/${id}/set-deal`, { discount_value: discountValue, discount_type: discountType })
}

export interface ListingEvent {
  id: string
  listing_id: string
  event_type: 'submitted' | 'approved' | 'rejected' | 'archived' | 'edit_submitted' | 'edit_approved' | 'edit_rejected'
  actor_name: string | null
  actor_email: string | null
  note: string | null
  snapshot_before: Record<string, unknown> | null
  snapshot_after: Record<string, unknown> | null
  created_at: string
}

export async function getListingHistory(listingId: string): Promise<ListingEvent[]> {
  const res = await client.get<ListingEvent[]>(`/admin/listings/${listingId}/history`)
  return res.data
}

export interface PlatformSettings {
  notify_email: string
  updated_at: string | null
}

export async function getPlatformSettings(): Promise<PlatformSettings> {
  const res = await client.get<PlatformSettings>('/admin/settings')
  return res.data
}

export async function updatePlatformSettings(data: { notify_email: string }): Promise<void> {
  await client.put('/admin/settings', data)
}

export async function changeUserRole(userId: string, role: string): Promise<void> {
  await client.put(`/admin/users/${userId}/role`, { role })
}

export interface BulkImportRowResult {
  row: number
  source_ref: string | null
  status: 'succeeded' | 'skipped' | 'failed'
  listing_id: string | null
  message: string | null
}

export interface BulkImportJob {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  total_rows: number
  processed_rows: number
  succeeded_count: number
  skipped_count: number
  failed_count: number
  results: BulkImportRowResult[]
  created_at: string
  completed_at: string | null
}

export async function bulkImportListings(csv: File): Promise<BulkImportJob> {
  const fd = new FormData()
  fd.append('csv_file', csv)
  const res = await client.post<BulkImportJob>('/admin/listings/bulk-import', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return res.data
}

export async function getBulkImportJob(jobId: string): Promise<BulkImportJob> {
  const res = await client.get<BulkImportJob>(`/admin/listings/bulk-import/${jobId}`)
  return res.data
}

export async function listBulkImportJobs(): Promise<BulkImportJob[]> {
  const res = await client.get<BulkImportJob[]>('/admin/listings/bulk-import')
  return res.data
}

export async function assignRealtorToOwner(userId: string, realtorId: string | null): Promise<void> {
  await client.put(`/admin/users/${userId}/assign-realtor`, { realtor_id: realtorId })
}
