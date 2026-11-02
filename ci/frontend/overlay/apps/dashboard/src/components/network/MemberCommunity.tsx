import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { createProfilePost, getMyNetworkProfile, getNetworkProfile, getNetworkProfiles, getProfilePosts, moderatePost, saveNetworkProfile, setNetworkBlock, type NetworkPage, type NetworkProfile } from '../../api/propertyNetwork'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, NetworkComments, NetworkComposer, NetworkDate, NetworkFreshness, NetworkPagination, NetworkPanel, PrivatePhotos, networkError, useNetworkDraftGuard, allowNetworkNavigation } from './NetworkShared'

export function MemberDirectory({ onOpen }: { onOpen: (code: number) => void }) {
  const { t } = useTranslation('property_network')
  const [query, setQuery] = useState(''), [search, setSearch] = useState(''), [page, setPage] = useState(1), [refresh, setRefresh] = useState(0)
  const [data, setData] = useState<NetworkPage<NetworkProfile> | null>(null), [error, setError] = useState('')
  useEffect(() => { const controller = new AbortController(); setData(null); setError(''); getNetworkProfiles({ q: search, page }, controller.signal).then(value => { if (!controller.signal.aborted) setData(value) }).catch(cause => { if (!controller.signal.aborted) setError(networkError(cause)) }); return () => controller.abort() }, [search, page, refresh])
  return <div className="space-y-5"><form className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); setSearch(query.trim()); setPage(1); setRefresh(value => value+1) }}><label className="min-w-0 flex-1 text-sm font-semibold text-ink">{t('search_members')}<input className={NETWORK_FIELD} value={query} maxLength={100} onChange={event => setQuery(event.target.value)} /></label><button className={NETWORK_BUTTON} type="submit">{t('search')}</button></form><p className="text-xs text-dim">{t('members_directory_note')}</p>
    {error ? <p role="alert" className="text-sm text-red-800">{t(error)}</p> : !data ? <p role="status">{t('loading')}</p> : <><div className="grid gap-4 md:grid-cols-2">{data.items.map(profile => <article key={profile.user_code} className="min-w-0 rounded-2xl border border-line bg-paper p-5"><h2 className="break-words text-xl font-bold text-ink">{profile.display_name}</h2><p className="mt-2 whitespace-pre-wrap break-words text-sm font-semibold text-sea">{profile.status_text}</p><p className="mt-3 whitespace-pre-wrap break-words text-sm text-ink2 line-clamp-3">{profile.bio}</p><button className={NETWORK_BUTTON+' mt-4'} onClick={() => onOpen(profile.user_code)}>{t('open_member')}</button></article>)}</div>{!data.items.length ? <p className="rounded-xl bg-paper p-5 text-sm text-dim">{t('members_empty')}</p> : null}<NetworkPagination page={page} total={data.total} busy={false} onChange={setPage} /></>}
  </div>
}

export function MemberProfile({ code, own = false, onBack }: { code?: number; own?: boolean; onBack?: () => void }) {
  const { t } = useTranslation('property_network')
  const [manualReload, setManualReload] = useState(0)
  const read = useNetworkRead(`profile:${own ? 'me' : code}:${manualReload}`, signal => own ? getMyNetworkProfile(signal) : getNetworkProfile(code!, signal))
  const profile = read.data, error = read.error ? networkError(read.error) : ''
  const isOwn = own || profile?.can_edit === true
  return <div className="space-y-5"><div className="flex flex-wrap gap-3">{onBack ? <button className={NETWORK_BUTTON} onClick={onBack}>{t('back_members')}</button> : null}<button className={NETWORK_BUTTON} onClick={() => { if (allowNetworkNavigation()) setManualReload(value => value+1) }}>{t('reload')}</button></div><NetworkFreshness read={read} />
    {error ? <p role="alert" className="text-sm text-red-800">{t(error)}</p> : null}{!profile ? !error ? <p role="status">{t('loading')}</p> : null : <>
      <header className="min-w-0 rounded-2xl bg-ink p-5 text-white"><p className="text-xs text-white/65">{t(profile.visibility === 'private' ? 'profile_private' : 'profile_members')}</p><h1 className="mt-2 break-words text-2xl font-bold">{profile.display_name}</h1><p className="mt-3 whitespace-pre-wrap break-words text-sm font-semibold text-white">{profile.status_text}</p><p className="mt-3 whitespace-pre-wrap break-words text-sm text-white/75">{profile.bio}</p>{own ? <p className="mt-3 text-xs text-white/65">{t('your_member_code', { code: profile.user_code })}</p> : null}</header>
      {isOwn ? <ProfileEdit key={profile.user_code} profile={profile} onSaved={read.refresh} /> : null}
      {isOwn && profile.version === 0 ? <p className="rounded-xl bg-paper p-5 text-sm text-ink2">{t('save_profile_first')}</p> : <ProfileWall key={profile.user_code} profile={profile} own={isOwn} />}
      <PrivacyControls own={isOwn} code={profile.user_code} onBlocked={onBack} />
    </>}
  </div>
}

function ProfileEdit({ profile, onSaved }: { profile: NetworkProfile; onSaved: () => void }) {
  const { t } = useTranslation('property_network')
  const [name, setName] = useState(profile.display_name), [bio, setBio] = useState(profile.bio), [status, setStatus] = useState(profile.status_text)
  const [visibility, setVisibility] = useState(profile.visibility), [wall, setWall] = useState(profile.wall_policy), [comments, setComments] = useState(profile.comments_enabled)
  const [baseline, setBaseline] = useState(profile)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const dirty = name !== baseline.display_name || bio !== baseline.bio || status !== baseline.status_text || visibility !== baseline.visibility || wall !== baseline.wall_policy || comments !== baseline.comments_enabled
  useNetworkDraftGuard(dirty, busy)
  useEffect(() => { if (!busy && !dirty && profile.version > baseline.version) { setName(profile.display_name); setBio(profile.bio); setStatus(profile.status_text); setVisibility(profile.visibility); setWall(profile.wall_policy); setComments(profile.comments_enabled); setBaseline(profile) } }, [profile, baseline.version, busy, dirty])
  async function save() {
    if (busy || error) return
    setBusy(true)
    try { const saved = await saveNetworkProfile({ expected_version: baseline.version, display_name: name.trim(), bio: bio.trim(), status_text: status.trim(), visibility, wall_policy: wall, comments_enabled: comments }); setName(saved.display_name); setBio(saved.bio); setStatus(saved.status_text); setVisibility(saved.visibility); setWall(saved.wall_policy); setComments(saved.comments_enabled); setBaseline(saved); onSaved() }
    catch (cause) { setError(networkError(cause)) }
    finally { setBusy(false) }
  }
  return <NetworkPanel title={t('edit_network_profile')}><p className="mb-4 text-xs text-dim">{t('profile_separate')}</p><form className="space-y-4" onSubmit={event => { event.preventDefault(); void save() }}><fieldset disabled={busy || !!error} className="space-y-4"><label className="block text-sm font-semibold">{t('display_name')}<input className={NETWORK_FIELD} value={name} onChange={event => setName(event.target.value)} required maxLength={100} /></label><label className="block text-sm font-semibold">{t('bio')}<textarea className={NETWORK_FIELD} rows={4} value={bio} onChange={event => setBio(event.target.value)} maxLength={2000} /></label><label className="block text-sm font-semibold">{t('status_text')}<textarea className={NETWORK_FIELD} rows={2} value={status} onChange={event => setStatus(event.target.value)} maxLength={280} /></label><div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm font-semibold">{t('visibility')}<select className={NETWORK_FIELD} value={visibility} onChange={event => setVisibility(event.target.value as typeof visibility)}><option value="private">{t('visibility_private')}</option><option value="members">{t('visibility_members')}</option></select></label><label className="block text-sm font-semibold">{t('wall_policy')}<select className={NETWORK_FIELD} value={wall} onChange={event => setWall(event.target.value as typeof wall)}><option value="own_only">{t('wall_own_only')}</option><option value="approval_required">{t('wall_approval_required')}</option></select></label></div><label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" checked={comments} onChange={event => setComments(event.target.checked)} />{t('enable_comments')}</label><p className="text-xs text-dim">{t('visibility_note')}</p><button type="submit" className={NETWORK_PRIMARY}>{t(busy ? 'saving' : 'save_changes')}</button></fieldset>{error ? <p role="alert" className="text-sm text-red-800">{t(error)} {t('reload_to_continue')}</p> : null}</form></NetworkPanel>
}

function ProfileWall({ profile, own }: { profile: NetworkProfile; own: boolean }) {
  const { t } = useTranslation('property_network')
  const [page, setPage] = useState(1), [writeError, setError] = useState(''), [busy, setBusy] = useState(false)
  const read = useNetworkRead(`wall:${profile.user_code}:${page}`, signal => getProfilePosts(profile.user_code, { page }, signal))
  const data = read.data, error = writeError || (read.error ? networkError(read.error) : '')
  async function moderate(id: string, action: 'approve' | 'hide') { setBusy(true); setError(''); try { await moderatePost(id, action); read.refresh() } catch (cause) { setError(networkError(cause)) } finally { setBusy(false) } }
  return <NetworkPanel title={t('wall')}><NetworkFreshness read={read} /><p className="text-xs text-dim">{t('wall_note')}</p>{own || profile.wall_policy === 'approval_required' ? <><p className="mt-4 text-sm font-semibold">{t(own ? 'your_wall_post' : 'wall_submission_note')}</p><NetworkComposer key={profile.user_code} photos submit={input => createProfilePost(profile.user_code, { submission_id: input.submission_id, body: input.body, media_ids: input.media_ids })} onSaved={() => { setPage(1); read.refresh() }} /></> : <p className="mt-3 text-sm text-dim">{t('wall_owner_only_note')}</p>}
    {error ? <p role="alert" className="mt-4 text-sm text-red-800">{t(error)}</p> : null}{!data && !error ? <p role="status" className="mt-4">{t('loading')}</p> : null}
    <div className="mt-5 space-y-5">{data?.items.map(post => <article key={post.id} className="min-w-0 rounded-xl border border-line bg-white p-4"><div className="flex flex-wrap justify-between gap-2"><p className="text-sm font-bold text-ink">{post.author.display_name}</p><p className="text-xs text-dim"><NetworkDate value={post.created_at} /></p></div><p className="mt-2 text-xs font-semibold text-sea">{t(`post_statuses.${post.status}`)}</p><p className="mt-3 whitespace-pre-wrap break-words text-sm text-ink2">{post.body}</p><PrivatePhotos ids={post.media_ids} />{post.can_moderate ? <div className="mt-3 flex flex-wrap gap-2">{post.status === 'pending' ? <button className={NETWORK_PRIMARY} disabled={busy} onClick={() => void moderate(post.id, 'approve')}>{t('approve_post')}</button> : null}{post.status !== 'hidden' ? <button className={NETWORK_BUTTON} disabled={busy} onClick={() => void moderate(post.id, 'hide')}>{t('hide_post')}</button> : null}</div> : null}<NetworkComments recordId={post.id} kind="post" canWrite={post.can_comment} mediaIds={post.media_ids} /></article>)}</div>
    {data && !data.items.length ? <p className="mt-4 text-sm text-dim">{t('wall_empty')}</p> : null}{data ? <NetworkPagination page={page} total={data.total} busy={busy} onChange={setPage} /> : null}
  </NetworkPanel>
}
function PrivacyControls({ own, code, onBlocked }: { own: boolean; code: number; onBlocked?: () => void }) {
  const { t } = useTranslation('property_network')
  const [target, setTarget] = useState(own ? '' : String(code)), [busy, setBusy] = useState(false), [error, setError] = useState(''), [saved, setSaved] = useState(false)
  async function setBlock(blocked: boolean) {
    if (!/^[1-9]\d{0,8}$/.test(target) || Number(target) === code && own) { setError('error_fields'); return }
    setBusy(true); setError(''); setSaved(false)
    try { await setNetworkBlock(Number(target), blocked); setSaved(true); if (blocked && !own) onBlocked?.() }
    catch (cause) { setError(networkError(cause)) }
    finally { setBusy(false) }
  }
  return <NetworkPanel title={t('privacy_controls')}><p className="mb-3 text-xs text-dim">{t('block_note')}</p><label className="block text-sm font-semibold">{t('member_code')}<input className={NETWORK_FIELD} type="number" min="1" max="999999999" step="1" value={target} disabled={!own || busy} onChange={event => { setTarget(event.target.value); setSaved(false) }} /></label><div className="mt-3 flex flex-wrap gap-3"><button className={NETWORK_BUTTON} disabled={busy || !target} onClick={() => void setBlock(true)}>{t('block_member')}</button>{own ? <button className={NETWORK_BUTTON} disabled={busy || !target} onClick={() => void setBlock(false)}>{t('unblock_member')}</button> : null}</div>{error ? <p role="alert" className="mt-3 text-sm text-red-800">{t(error)}</p> : null}{saved ? <p role="status" className="mt-3 text-sm text-sea">{t('block_saved')}</p> : null}</NetworkPanel>
}
