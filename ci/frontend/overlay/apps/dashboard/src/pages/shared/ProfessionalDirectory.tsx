import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { isAxiosError } from 'axios'
import { useTranslation } from 'react-i18next'
import { emptyProfessionalOptions, professionalLabel, professionalOptionLabels, professionalServiceLabels, type ProfessionalOption, type ProfessionalOptions, type ProfessionalPage, type PublicProfessional } from '../../../../../packages/professionals/src'
import { getProfessional, getProfessionalOptions, getProfessionals } from '../../api/professionals'
import { ProfessionalDialog } from '../../components/shared/ProfessionalDialog'
import { ProfessionalPreview } from '../../components/shared/ProfessionalPreview'
import { professionalButton, professionalInput, professionalPrimary } from '../../components/shared/ProfessionalFields'
import { landingBaseUrl } from '../../lib/siteUrls'

const LIMIT = 24
const LANDING_URL = landingBaseUrl(import.meta.env.VITE_LANDING_URL)
const emptyPage: ProfessionalPage<PublicProfessional> = { items: [], total: 0, limit: LIMIT, offset: 0 }

function FilterChoices({ title, options, values, change, max }: { title: string; options: ProfessionalOption[]; values: string[]; change: (values: string[]) => void; max: number }) {
  const { t, i18n } = useTranslation('professionals')
  return <details className="min-w-0 rounded-xl border border-line bg-white p-3">
    <summary className="cursor-pointer text-sm font-semibold text-ink">{title}<span className="ml-2 text-xs font-normal text-dim">{t('directory_search.selected', { count: values.length })}</span></summary>
    <fieldset className="mt-3 grid gap-2 max-h-64 overflow-y-auto"><legend className="sr-only">{title}</legend>
      {options.map(option => <label key={option.id} className="flex items-start gap-2 text-sm text-ink2"><input type="checkbox" className="mt-1 shrink-0 accent-coral" checked={values.includes(option.id)} disabled={!values.includes(option.id) && values.length >= max} onChange={event => change(event.target.checked ? [...values, option.id] : values.filter(value => value !== option.id))} /><span>{professionalLabel(option.label, i18n.language)}</span></label>)}
    </fieldset>
  </details>
}

export default function ProfessionalDirectory() {
  const { t, i18n } = useTranslation('professionals')
  const [params, setParams] = useSearchParams()
  const paramsString = params.toString()
  const filters = useMemo(() => {
    const parsed = new URLSearchParams(paramsString)
    const rawOffset = Number(parsed.get('offset') ?? 0)
    return { q: (parsed.get('q') ?? '').slice(0, 100), language: (parsed.get('language') ?? '').slice(0, 40), category: [...new Set(parsed.getAll('category'))], area: [...new Set(parsed.getAll('area'))],
      offset: Number.isFinite(rawOffset) && rawOffset >= 0 ? Math.min(9984, Math.floor(rawOffset / LIMIT) * LIMIT) : 0 }
  }, [paramsString])
  const filterKey = JSON.stringify(filters)
  const selectedId = params.get('profile') ?? ''
  const [query, setQuery] = useState(filters.q)
  const [categories, setCategories] = useState(filters.category)
  const [areas, setAreas] = useState(filters.area)
  const [language, setLanguage] = useState(filters.language)
  const [options, setOptions] = useState<ProfessionalOptions>(emptyProfessionalOptions)
  const [page, setPage] = useState(emptyPage)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [authError, setAuthError] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [detail, setDetail] = useState<PublicProfessional | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState(false)

  useEffect(() => {
    const requested = JSON.parse(filterKey) as typeof filters
    setQuery(requested.q); setCategories(requested.category); setAreas(requested.area); setLanguage(requested.language)
  }, [filterKey])
  useEffect(() => {
    const controller = new AbortController()
    const requested = JSON.parse(filterKey) as typeof filters
    setLoading(true); setError(false); setAuthError(false); setPage(emptyPage)
    Promise.all([getProfessionalOptions(controller.signal), getProfessionals({ ...requested, language: requested.language || undefined, limit: LIMIT }, controller.signal)])
      .then(([nextOptions, next]) => { if (!controller.signal.aborted) { setOptions(nextOptions); setPage(next) } })
      .catch(cause => { if (!controller.signal.aborted) { setError(true); setAuthError(isAxiosError(cause) && [401, 403].includes(cause.response?.status ?? 0)); setDetail(null) } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [filterKey, refresh])
  useEffect(() => {
    setDetail(null); setDetailError(false)
    if (!selectedId || authError) return
    const controller = new AbortController()
    setDetailLoading(true)
    getProfessional(selectedId, controller.signal)
      .then(next => { if (!controller.signal.aborted) setDetail(next) })
      .catch(cause => { if (!controller.signal.aborted) { setDetailError(true); if (isAxiosError(cause) && [401, 403].includes(cause.response?.status ?? 0)) { setAuthError(true); setPage(emptyPage) } } })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false) })
    return () => controller.abort()
  }, [selectedId, authError])
  function apply(clear = false) {
    const next = new URLSearchParams(paramsString)
    for (const key of ['q', 'category', 'area', 'language', 'offset', 'profile']) next.delete(key)
    if (!clear) { if (query.trim()) next.set('q', query.trim()); if (language) next.set('language', language); categories.forEach(id => next.append('category', id)); areas.forEach(id => next.append('area', id)) }
    setParams(next, { replace: true })
  }
  function select(id?: string) { const next = new URLSearchParams(paramsString); id ? next.set('profile', id) : next.delete('profile'); setParams(next, { replace: true }) }
  function paginate(offset: number) { const next = new URLSearchParams(paramsString); next.delete('profile'); offset ? next.set('offset', String(offset)) : next.delete('offset'); setParams(next, { replace: true }) }
  return <div className="min-w-0 space-y-6">
    <header className="rounded-2xl bg-ink p-5 sm:p-7 text-white"><h1 className="text-2xl font-bold">{t('directory_search.title')}</h1><p className="mt-3 max-w-3xl text-sm text-white/75 leading-relaxed">{t('directory_search.intro')}</p><a href="/professional-profile" className="inline-block mt-4 text-sm font-bold text-gold underline">{t('directory_search.join')}</a></header>
    <form onSubmit={event => { event.preventDefault(); apply() }} className="rounded-2xl border border-line bg-white p-4 sm:p-6 space-y-4">
      <label className="block text-sm font-bold text-ink"><span className="block mb-2">{t('directory_search.search')}</span><input maxLength={100} value={query} onChange={event => setQuery(event.target.value)} placeholder={t('directory_search.search_placeholder')} className={professionalInput} /></label>
      <div className="grid sm:grid-cols-2 gap-3 items-start"><FilterChoices title={t('categories')} options={options.categories} values={categories} change={setCategories} max={8} /><FilterChoices title={t('areas')} options={options.areas} values={areas} change={setAreas} max={20} /></div>
      <label className="block text-sm font-semibold text-ink"><span className="mb-2 block">{t('languages')}</span><select className={professionalInput} value={language} onChange={event => setLanguage(event.target.value)}><option value="">{t('directory_language_any')}</option>{options.languages.map(option => <option key={option.id} value={option.id}>{professionalLabel(option.label, i18n.language)}</option>)}</select></label>
      <p className="text-xs text-dim">{t('directory_search.filter_help')}</p>
      <div className="flex flex-wrap gap-3"><button type="submit" className={professionalPrimary}>{t('directory_search.apply')}</button><button type="button" className={professionalButton} onClick={() => { setQuery(''); setCategories([]); setAreas([]); setLanguage(''); apply(true) }}>{t('directory_search.clear')}</button></div>
    </form>
    {authError ? <div role="alert" className="rounded-xl border border-line bg-white p-5 space-y-3"><p>{t('directory_search.auth_error')}</p><a className={professionalPrimary} href={`${LANDING_URL.replace(/\/$/, '')}/login/?directory=professionals`}>{t('directory_search.login')}</a></div> : loading ? <p role="status" className="text-sm text-ink2">{t('directory_search.loading')}</p> : error ? <div role="alert" className="rounded-xl border border-line p-5 space-y-3"><p>{t('directory_search.error')}</p><button className={professionalButton} onClick={() => setRefresh(value => value + 1)}>{t('retry')}</button></div> : <>
      <p role="status" className="text-sm text-dim">{t('directory_search.results', { count: page.total })}</p>
      {!page.items.length && <div className="rounded-2xl border border-line bg-white p-6"><h2 className="font-bold text-lg text-ink">{t('directory_search.empty')}</h2><p className="text-sm text-ink2 mt-2">{t('directory_search.empty_help')}</p></div>}
      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-5">{page.items.map(profile => <article key={profile.id} className="min-w-0 rounded-2xl border border-line bg-white p-5 flex flex-col gap-4">
        <div className="flex gap-3 items-start">{profile.photo_url && <img src={profile.photo_url} alt="" loading="lazy" className="h-16 w-16 rounded-xl object-cover shrink-0" />}<h2 className="text-xl font-bold text-ink break-words">{profile.business_name || profile.contact_name}</h2></div>
        {profile.contact_name && profile.contact_name !== profile.business_name && <p className="text-sm text-ink2">{profile.contact_name}</p>}
        <p className="break-words text-xs font-semibold text-coral">{professionalServiceLabels(profile, options.categories, i18n.language).join(' · ')}</p>
        <p className="text-xs text-dim">{professionalOptionLabels(profile.service_area_ids, options.areas, i18n.language).join(' · ')}</p>
        <p className="line-clamp-3 whitespace-pre-line text-sm leading-relaxed text-ink2 break-words">{professionalLabel(profile.descriptions, i18n.language)}</p>
        <button type="button" className={`${professionalButton} mt-auto self-start`} onClick={() => select(profile.id)} aria-label={`${t('directory_search.view')}: ${profile.business_name || profile.contact_name}`}>{t('directory_search.view')}</button>
      </article>)}</div>
      {page.total > LIMIT && <nav aria-label={t('directory_search.browse')} className="flex flex-wrap items-center gap-3"><button className={professionalButton} disabled={!filters.offset} onClick={() => paginate(Math.max(0, filters.offset - LIMIT))}>{t('directory_search.previous')}</button><span className="text-xs text-dim">{t('directory_search.page', { page: Math.floor(filters.offset / LIMIT) + 1, pages: Math.max(1, Math.ceil(page.total / LIMIT)) })}</span><button className={professionalButton} disabled={filters.offset + LIMIT >= page.total || filters.offset + LIMIT > 9984} onClick={() => paginate(filters.offset + LIMIT)}>{t('directory_search.next')}</button></nav>}
    </>}
    <p className="text-xs leading-relaxed text-dim">{t('directory_search.review_note')}</p>
    {selectedId && !authError && <ProfessionalDialog title={detail?.business_name || detail?.contact_name || t('directory_search.title')} closeLabel={t('directory_search.close')} onClose={() => select()}>
      {detailError ? <p role="alert">{t('directory_search.unavailable')}</p> : detailLoading || !detail ? <p role="status">{t('directory_search.loading')}</p> : <ProfessionalPreview key={detail.id} profile={detail} options={options} shareableBusiness />}
    </ProfessionalDialog>}
  </div>
}
