import client from './axios'
import type { AdminProfessional, AdminProfessionalSummary, ProfessionalDraft, ProfessionalOptions, ProfessionalPage, ProfessionalProfile, PublicProfessional } from '../../../../packages/professionals/src'

export async function getProfessionalOptions(signal?: AbortSignal): Promise<ProfessionalOptions> {
  return (await client.get<ProfessionalOptions>('/professionals/options', { signal })).data
}
export async function getProfessionals(params: { category: string[]; area: string[]; language?: string; q: string; limit: number; offset: number }, signal?: AbortSignal): Promise<ProfessionalPage<PublicProfessional>> {
  return (await client.get<ProfessionalPage<PublicProfessional>>('/professionals', { params, paramsSerializer: { indexes: null }, signal })).data
}
export async function getProfessional(id: string, signal?: AbortSignal): Promise<PublicProfessional> {
  return (await client.get<PublicProfessional>(`/professionals/${encodeURIComponent(id)}`, { signal })).data
}
export async function getMyProfessionalProfile(): Promise<ProfessionalProfile | null> {
  return (await client.get<ProfessionalProfile | null>('/professional-profile')).data
}
export async function saveProfessionalDraft(expected_version: number, draft: ProfessionalDraft): Promise<ProfessionalProfile> {
  return (await client.put<ProfessionalProfile>('/professional-profile', { expected_version, draft })).data
}
export async function submitProfessionalProfile(expected_version: number): Promise<ProfessionalProfile> {
  return (await client.post<ProfessionalProfile>('/professional-profile/submit', { expected_version })).data
}
export async function withdrawProfessionalProfile(expected_version: number): Promise<ProfessionalProfile> {
  return (await client.post<ProfessionalProfile>('/professional-profile/withdraw', { expected_version })).data
}
export async function uploadProfessionalPhoto(file: File): Promise<string> {
  const form = new FormData()
  form.append('file', file)
  return (await client.post<{ photo_url: string }>('/professional-profile/photo', form)).data.photo_url
}
export async function getAdminProfessionals(params: { review_status?: string; listing_status?: string; q?: string; limit: number; offset: number }, signal?: AbortSignal): Promise<ProfessionalPage<AdminProfessionalSummary>> {
  return (await client.get<ProfessionalPage<AdminProfessionalSummary>>('/admin/professionals', { params, signal })).data
}
export async function getAdminProfessional(id: string): Promise<AdminProfessional> {
  return (await client.get<AdminProfessional>(`/admin/professionals/${encodeURIComponent(id)}`)).data
}
export async function createAdminProfessional(draft: ProfessionalDraft): Promise<AdminProfessional> {
  return (await client.post<AdminProfessional>('/admin/professionals', { draft })).data
}
export async function saveAdminProfessional(id: string, expected_version: number, draft: ProfessionalDraft): Promise<AdminProfessional> {
  return (await client.put<AdminProfessional>(`/admin/professionals/${encodeURIComponent(id)}`, { expected_version, draft })).data
}
export async function submitAdminProfessional(id: string, expected_version: number): Promise<AdminProfessional> {
  return (await client.post<AdminProfessional>(`/admin/professionals/${encodeURIComponent(id)}/submit`, { expected_version })).data
}
export async function uploadAdminProfessionalPhoto(id: string, file: File): Promise<string> {
  const form = new FormData()
  form.append('file', file)
  return (await client.post<{ photo_url: string }>(`/admin/professionals/${encodeURIComponent(id)}/photo`, form)).data.photo_url
}
export async function moderateProfessional(id: string, action: 'approve' | 'reject' | 'suspend' | 'reinstate', expected_version: number, reason?: string): Promise<AdminProfessional> {
  return (await client.post<AdminProfessional>(`/admin/professionals/${encodeURIComponent(id)}/${action}`, { expected_version, ...(reason ? { note: reason } : {}) })).data
}
