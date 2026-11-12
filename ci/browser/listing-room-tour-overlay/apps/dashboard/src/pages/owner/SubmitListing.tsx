import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, ImagePlus, X, Star, Plus } from 'lucide-react'
import toast from 'react-hot-toast'
import { submitListing, updateListing, uploadListingImages, type Listing } from '../../api/listings'
import { RoomPhotoTourEditor } from '../../components/listings/RoomPhotoTourEditor'
import { RichTextEditor } from '../../components/RichTextEditor'
import { adminListingAction } from '../../lib/listingPublication'

const REGIONS = [
  'Cap Cana', 'Cabarete', 'Jarabacoa', 'Las Terrenas', 'Punta Cana',
  'Puerto Plata', 'Samaná', 'Santo Domingo', 'Santiago', 'Sosúa',
]
const TYPES = ['villa', 'apartment', 'condo', 'land', 'commercial']
const FEATURES = [
  'Pool', 'Ocean View', 'Beachfront', 'Oceanfront', 'Furnished', 'Beach Access', 'Mountain View',
  'Parking', 'Gym', 'Smart Home', 'Backup Generator', 'Solar Panels',
]
const ALL_TAGS = ['Luxury', 'New', 'Investment', 'Pet Friendly', 'Short Term', 'Furnished', 'Ocean View', 'Mountain View']
const INCLUDED_UTILITIES = ['Water', 'Electricity', 'Gas', 'WiFi', 'Cable TV', 'Trash Removal', 'Pool Maintenance', 'Gardening', 'Security', 'Parking', 'Laundry']

const inp = 'w-full px-3 py-2.5 rounded-lg border border-line bg-white text-[13.5px] text-ink outline-none transition-colors focus:border-[#f0a800]'

function Sec({ n, title, tone, children }: { n: number; title: string; tone: string; children: React.ReactNode }) {
  return (
    <div className="bg-paper border border-line rounded-2xl px-6 py-5">
      <div className="flex items-center gap-3 mb-5">
        <div className="w-6 h-6 rounded-full text-white text-[11px] font-bold grid place-items-center shrink-0" style={{ background: tone }}>
          {n}
        </div>
        <div className="text-[14px] font-bold text-ink">{title}</div>
        <div className="flex-1 h-px bg-line-soft" />
      </div>
      {children}
    </div>
  )
}

function Lbl({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide mb-1.5">{children}</div>
  )
}

function Toggle({ value, onChange, label, tone }: { value: boolean; onChange: (v: boolean) => void; label: string; tone: string }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!value)}
      className="flex items-center gap-3 px-4 py-2.5 rounded-xl border cursor-pointer text-[13px] font-semibold w-full text-left transition-all"
      style={{ borderColor: value ? tone : '#e2e8f0', background: value ? `${tone}0d` : 'white', color: value ? tone : '#64748b' }}
    >
      <div className="w-9 h-5 rounded-full relative shrink-0 transition-colors duration-150" style={{ background: value ? tone : '#cbd5e1' }}>
        <div className="absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-all duration-150" style={{ left: value ? '1.25rem' : '0.125rem' }} />
      </div>
      {label}
    </button>
  )
}

function parseLatLng(url: string): { lat: string; lng: string } | null {
  const atMatch = url.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/)
  if (atMatch) return { lat: atMatch[1], lng: atMatch[2] }
  const qMatch = url.match(/[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)/)
  if (qMatch) return { lat: qMatch[1], lng: qMatch[2] }
  return null
}

const EMPTY_FORM = {
  title: '', type: 'villa', location: '', price_per_day: '',
  description: '', bedrooms: '', bathrooms: '', area_sqft: '', lot_size_sqft: '',
  construction_status: '', year_built: '',
  gated_community: false,
  features: [] as string[],
  included_utilities: [] as string[],
  tags: [] as string[],
  video_links: [] as string[],
  tour_3d_url: '', maps_url: '', latitude: '', longitude: '',
}

function listingToForm(l: Listing): typeof EMPTY_FORM {
  return {
    title:               l.title,
    type:                l.type,
    location:            l.location,
    price_per_day:       l.price_per_day != null ? String(Math.round(l.price_per_day)) : '',
    description:         l.description ?? '',
    bedrooms:            l.bedrooms   != null ? String(l.bedrooms)   : '',
    bathrooms:           l.bathrooms  != null ? String(l.bathrooms)  : '',
    area_sqft:           l.area_sqft  != null ? String(l.area_sqft)  : '',
    lot_size_sqft:       l.lot_size_sqft != null ? String(l.lot_size_sqft) : '',
    construction_status: l.construction_status ?? '',
    year_built:          l.year_built != null ? String(l.year_built) : '',
    gated_community:     l.gated_community,
    features:            l.features ?? [],
    included_utilities:  l.included_utilities ?? [],
    tags:                l.tags ?? [],
    video_links:         l.video_links ?? [],
    tour_3d_url:         l.tour_3d_url ?? '',
    maps_url:            l.maps_url ?? '',
    latitude:            l.latitude  != null ? String(l.latitude)  : '',
    longitude:           l.longitude != null ? String(l.longitude) : '',
  }
}

export function OwnerSubmitListing({ go, tone, listing, onBack, isAdmin = false }: { go: (v: string) => void; tone: string; listing?: Listing; onBack?: () => void; isAdmin?: boolean }) {
  const isEdit = Boolean(listing)
  const adminAction = adminListingAction(isEdit, listing?.status)
  const { t } = useTranslation('owner')
  const [form, setForm] = useState(listing ? listingToForm(listing) : EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [uploadedUrls, setUploadedUrls] = useState<string[]>(listing?.images ?? [])
  const [thumbnail, setThumbnail] = useState<string | null>(listing?.images?.[0] ?? null)
  const [uploading, setUploading] = useState(false)
  const [dayCurrency, setDayCurrency] = useState<'USD' | 'DOP'>('USD')
  const [dopRate, setDopRate] = useState(59.5)
  const [customInput, setCustomInput] = useState('')
  const [customUtilityInput, setCustomUtilityInput] = useState('')
  const [customTagInput, setCustomTagInput] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    fetch('https://open.er-api.com/v6/latest/USD')
      .then(r => r.json())
      .then(d => { if (d.rates?.DOP) setDopRate(d.rates.DOP) })
      .catch(() => {})
  }, [])

  function set(f: string, v: unknown) { setForm(p => ({ ...p, [f]: v })) }

  function toggleFeature(f: string) {
    set('features', form.features.includes(f) ? form.features.filter(x => x !== f) : [...form.features, f])
  }

  function toggleIncluded(u: string) {
    set('included_utilities', form.included_utilities.includes(u)
      ? form.included_utilities.filter(x => x !== u)
      : [...form.included_utilities, u])
  }

  function addCustomFeature() {
    const val = customInput.trim().toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    if (val && !form.features.includes(val)) set('features', [...form.features, val])
    setCustomInput('')
  }

  function addCustomUtility() {
    const toTitle = (s: string) => s.trim().toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    const entries = customUtilityInput.split(',').map(toTitle).filter(s => s && !INCLUDED_UTILITIES.includes(s) && !form.included_utilities.includes(s))
    if (!entries.length) return
    set('included_utilities', [...form.included_utilities, ...entries])
    setCustomUtilityInput('')
  }

  function removeCustomUtility(u: string) {
    set('included_utilities', form.included_utilities.filter(x => x !== u))
  }

  function addCustomTag() {
    const val = customTagInput.trim().toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    if (val && !form.tags.includes(val)) set('tags', [...form.tags, val])
    setCustomTagInput('')
  }

  function addVideoLink() { set('video_links', [...form.video_links, '']) }
  function updateVideoLink(i: number, val: string) { const arr = [...form.video_links]; arr[i] = val; set('video_links', arr) }
  function removeVideoLink(i: number) { set('video_links', form.video_links.filter((_, j) => j !== i)) }

  function handleDayCurrencyToggle(c: 'USD' | 'DOP') {
    const raw = parseFloat(form.price_per_day || '0') || 0
    if (c === 'DOP' && dayCurrency === 'USD') set('price_per_day', Math.round(raw * dopRate).toString())
    else if (c === 'USD' && dayCurrency === 'DOP') set('price_per_day', Math.round(raw / dopRate).toString())
    setDayCurrency(c)
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    if (!files.length) return
    if (uploadedUrls.length + files.length > 25) { toast.error(t('submit_listing_page.toast_max_photos')); return }
    setUploading(true)
    try {
      const newUrls = await uploadListingImages(files)
      setUploadedUrls(prev => {
        const merged = [...prev, ...newUrls]
        if (!thumbnail) setThumbnail(merged[0])
        return merged
      })
    } catch {
      toast.error(t('submit_listing_page.toast_upload_error'))
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  function removeImage(url: string) {
    setUploadedUrls(prev => {
      const next = prev.filter(u => u !== url)
      if (thumbnail === url) setThumbnail(next[0] ?? null)
      return next
    })
  }

  function goBack() { if (onBack) onBack(); else go('listings') }

  async function handleSubmit(e: React.SyntheticEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!form.title.trim() || !form.location || !form.price_per_day) {
      toast.error(t('submit_listing_page.toast_validation')); return
    }
    setSubmitting(true)
    try {
      const dailyUSD = dayCurrency === 'DOP' ? Math.round(parseFloat(form.price_per_day) / dopRate) : parseFloat(form.price_per_day)
      const orderedImages = thumbnail ? [thumbnail, ...uploadedUrls.filter(u => u !== thumbnail)] : uploadedUrls
      const payload = {
        title:               form.title.trim(),
        description:         form.description || undefined,
        type:                form.type,
        transaction:         'rent' as const,
        price:               dailyUSD,
        price_per_day:       dailyUSD,
        location:            form.location,
        bedrooms:            form.bedrooms      ? parseInt(form.bedrooms)      : undefined,
        bathrooms:           form.bathrooms     ? parseFloat(form.bathrooms)   : undefined,
        area_sqft:           form.area_sqft     ? parseInt(form.area_sqft)     : undefined,
        lot_size_sqft:       form.lot_size_sqft ? parseInt(form.lot_size_sqft) : undefined,
        construction_status: form.construction_status || undefined,
        year_built:          form.year_built    ? parseInt(form.year_built)    : undefined,
        gated_community:     form.gated_community,
        features:            form.features.length ? form.features : undefined,
        included_utilities:  form.included_utilities.length ? form.included_utilities : undefined,
        tags:                form.tags.length ? form.tags : undefined,
        video_links:         form.video_links.filter(v => v.trim()).length ? form.video_links.filter(v => v.trim()) : undefined,
        tour_3d_url:         form.tour_3d_url.trim() || undefined,
        maps_url:            form.maps_url.trim() || undefined,
        latitude:            form.latitude  ? parseFloat(form.latitude)  : undefined,
        longitude:           form.longitude ? parseFloat(form.longitude) : undefined,
        images:              orderedImages.length ? orderedImages : undefined,
      }
      if (isEdit && listing) {
        await updateListing(listing.id, payload)
        toast.success(t(isAdmin ? adminAction.toast : 'submit_listing_page.toast_edit_success'))
        goBack()
      } else {
        await submitListing(payload)
        toast.success(t(isAdmin ? adminAction.toast : 'submit_listing_page.toast_success'))
        setForm(EMPTY_FORM); setUploadedUrls([]); setThumbnail(null)
        go('listings')
      }
    } catch {
      toast.error(t('submit_listing_page.toast_error'))
    } finally {
      setSubmitting(false)
    }
  }

  const customFeatures = form.features.filter(f => !FEATURES.includes(f))
  const customUtilities = form.included_utilities.filter(u => !INCLUDED_UTILITIES.includes(u))
  const customTags = form.tags.filter(t => !ALL_TAGS.includes(t))

  let sn = 0
  const n = () => ++sn

  return (
    <div className="max-w-3xl mx-auto">
      <button
        type="button"
        onClick={goBack}
        className="flex items-center gap-1.5 text-dim text-[13px] mb-6 bg-transparent border-0 cursor-pointer hover:text-ink transition-colors"
      >
        <ArrowLeft size={14} /> {t('submit_listing_page.back')}
      </button>

      {listing?.status === 'active' && <RoomPhotoTourEditor key={listing.id} listingId={listing.id} />}

      {/* Pending approval notice */}
      <div className="mb-5 flex items-start gap-3 px-4 py-3.5 rounded-xl border text-[13px]"
        style={{ background: `${tone}0a`, borderColor: `${tone}40`, color: '#78716c' }}>
        <svg className="shrink-0 mt-0.5" width="15" height="15" viewBox="0 0 15 15" fill="none">
          <circle cx="7.5" cy="7.5" r="7" stroke={tone} strokeWidth="1.3" />
          <path d="M7.5 5v3.5M7.5 10.5v.5" stroke={tone} strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <span>{t(isAdmin ? adminAction.note : 'submit_listing_page.notice_pending')}</span>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* 1 — Property Info */}
        <Sec n={n()} title={t('submit_listing_page.sec_basic_info')} tone={tone}>
          <div className="space-y-4">
            <div>
              <Lbl>{t('submit_listing_page.field_title')}</Lbl>
              <input
                className={inp}
                value={form.title}
                onChange={e => set('title', e.target.value)}
                placeholder="e.g. Oceanfront Villa with Pool"
                required
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Lbl>{t('submit_listing_page.field_type')}</Lbl>
                <select className={inp + ' cursor-pointer'} value={form.type} onChange={e => set('type', e.target.value)}>
                  {TYPES.map(tp => (
                    <option key={tp} value={tp}>{t(`submit_listing_page.type_${tp}`, { defaultValue: tp.charAt(0).toUpperCase() + tp.slice(1) })}</option>
                  ))}
                </select>
              </div>
              <div>
                <Lbl>{t('submit_listing_page.field_region')}</Lbl>
                <select className={inp + ' cursor-pointer'} value={form.location} onChange={e => set('location', e.target.value)} required>
                  <option value="">{t('submit_listing_page.select_region')}</option>
                  {REGIONS.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
            </div>
            <div>
              <Lbl>{t('submit_listing_page.field_description')}</Lbl>
              <RichTextEditor value={form.description} onChange={v => set('description', v)} tone={tone} />
            </div>
          </div>
        </Sec>

        {/* 2 — Property Details */}
        <Sec n={n()} title={t('submit_listing_page.sec_property_details')} tone={tone}>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              { key: 'bedrooms',      label: t('submit_listing_page.field_bedrooms'),    ph: '3',    step: '1'   },
              { key: 'bathrooms',     label: t('submit_listing_page.field_bathrooms'),   ph: '1.5',  step: '0.5' },
              { key: 'area_sqft',     label: t('submit_listing_page.field_living_area'), ph: '2400', step: '1'   },
              { key: 'lot_size_sqft', label: t('submit_listing_page.field_lot_size'),    ph: '5000', step: '1'   },
            ].map(({ key, label, ph, step }) => (
              <div key={key}>
                <Lbl>{label}</Lbl>
                <input
                  className={inp}
                  type="number"
                  step={step}
                  value={(form as Record<string, unknown>)[key] as string}
                  onChange={e => set(key, e.target.value)}
                  placeholder={ph}
                  min="0"
                />
              </div>
            ))}
          </div>
        </Sec>

        {/* 3 — Construction */}
        <Sec n={n()} title={t('submit_listing_page.sec_construction')} tone={tone}>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <Lbl>{t('submit_listing_page.field_construction')}</Lbl>
              <select className={inp + ' cursor-pointer'} value={form.construction_status} onChange={e => set('construction_status', e.target.value)}>
                <option value="">{t('submit_listing_page.status_not_specified')}</option>
                <option value="pre_construction">{t('submit_listing_page.status_pre_con')}</option>
                <option value="under_construction">{t('submit_listing_page.status_under_con')}</option>
                <option value="completed">{t('submit_listing_page.status_completed')}</option>
              </select>
            </div>
            {form.construction_status === 'completed' && (
              <div>
                <Lbl>{t('submit_listing_page.field_year_built')}</Lbl>
                <input
                  className={inp}
                  type="number"
                  value={form.year_built}
                  onChange={e => set('year_built', e.target.value)}
                  placeholder="e.g. 2019"
                  min="1900"
                  max={new Date().getFullYear()}
                />
              </div>
            )}
          </div>
        </Sec>

        {/* 4 — Nightly Rate */}
        <Sec n={n()} title={t('submit_listing_page.sec_nightly_rate')} tone={tone}>
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide">{t('submit_listing_page.field_daily_rate')}</div>
              <div className="flex rounded-lg border border-line overflow-hidden text-[11px] font-bold">
                {(['USD', 'DOP'] as const).map(c => (
                  <button key={c} type="button" onClick={() => handleDayCurrencyToggle(c)} className="px-2.5 py-1 transition-colors cursor-pointer"
                    style={{ background: dayCurrency === c ? tone : 'white', color: dayCurrency === c ? 'white' : '#64748b' }}>{c}</button>
                ))}
              </div>
            </div>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-dim text-[13px]">{dayCurrency === 'USD' ? '$' : 'RD$'}</span>
              <input
                className={inp + (dayCurrency === 'USD' ? ' pl-6' : ' pl-11')}
                type="text"
                inputMode="numeric"
                value={form.price_per_day ? Number(form.price_per_day).toLocaleString('en-US') : ''}
                onChange={e => set('price_per_day', e.target.value.replace(/[^0-9]/g, ''))}
                placeholder={dayCurrency === 'USD' ? 'e.g. 150' : 'e.g. 8,900'}
                required
              />
            </div>
            <p className="text-[11.5px] text-dim mt-1">
              {form.price_per_day && parseFloat(form.price_per_day) > 0
                ? dayCurrency === 'USD'
                  ? `≈ DOP ${Math.round(parseFloat(form.price_per_day) * dopRate).toLocaleString('en-US')}`
                  : `≈ USD ${Math.round(parseFloat(form.price_per_day) / dopRate).toLocaleString('en-US')}`
                : 'Daily rate for short-term, vacation rentals'}
            </p>
          </div>
        </Sec>

        {/* 5 — Community & Features */}
        <Sec n={n()} title={t('submit_listing_page.sec_community')} tone={tone}>
          <div className="space-y-4">
            <Toggle value={form.gated_community} onChange={v => set('gated_community', v)} label={t('submit_listing_page.field_gated')} tone={tone} />
            <div>
              <Lbl>{t('submit_listing_page.field_features')}</Lbl>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mt-1">
                {FEATURES.map(f => {
                  const active = form.features.includes(f)
                  return (
                    <button key={f} type="button" onClick={() => toggleFeature(f)}
                      className="flex items-center gap-2 px-3 py-2 rounded-lg border text-[13px] cursor-pointer transition-all text-left"
                      style={{ borderColor: active ? tone : '#e2e8f0', background: active ? `${tone}0d` : 'white', color: active ? tone : '#64748b', fontWeight: active ? 600 : 400 }}>
                      <div className="w-3.5 h-3.5 rounded border shrink-0 grid place-items-center transition-colors"
                        style={{ borderColor: active ? tone : '#cbd5e1', background: active ? tone : 'white' }}>
                        {active && <svg width="8" height="6" viewBox="0 0 8 6" fill="none"><path d="M1 3L3 5L7 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>}
                      </div>
                      {f}
                    </button>
                  )
                })}
              </div>
              {customFeatures.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {customFeatures.map(f => (
                    <span key={f} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-semibold border"
                      style={{ borderColor: tone, background: `${tone}0d`, color: tone }}>
                      {f}
                      <button type="button" onClick={() => toggleFeature(f)}
                        className="ml-0.5 rounded-full w-3.5 h-3.5 grid place-items-center text-[10px] cursor-pointer border-0"
                        style={{ background: tone, color: 'white' }}>×</button>
                    </span>
                  ))}
                </div>
              )}
              <div className="flex gap-2 mt-2">
                <input className={inp + ' flex-1'} type="text" value={customInput}
                  onChange={e => setCustomInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCustomFeature() } }}
                  placeholder={t('submit_listing_page.add_custom_feature')} maxLength={50} />
                <button type="button" onClick={addCustomFeature} disabled={!customInput.trim()}
                  className="px-4 py-2.5 rounded-lg text-[13px] font-semibold border-0 cursor-pointer disabled:opacity-40 transition-opacity"
                  style={{ background: tone, color: 'white' }}>
                  {t('submit_listing_page.add')}
                </button>
              </div>
            </div>
          </div>
        </Sec>

        {/* 6 — What's Included */}
        <Sec n={n()} title={t('submit_listing_page.sec_included')} tone={tone}>
          <p className="text-[12.5px] text-dim mb-3">{t('submit_listing_page.field_included_sub')}</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {INCLUDED_UTILITIES.map(u => {
              const active = form.included_utilities.includes(u)
              return (
                <button key={u} type="button" onClick={() => toggleIncluded(u)}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg border text-[13px] cursor-pointer transition-all text-left"
                  style={{ borderColor: active ? tone : '#e2e8f0', background: active ? `${tone}0d` : 'white', color: active ? tone : '#64748b', fontWeight: active ? 600 : 400 }}>
                  <div className="w-3.5 h-3.5 rounded border shrink-0 grid place-items-center transition-colors"
                    style={{ borderColor: active ? tone : '#cbd5e1', background: active ? tone : 'white' }}>
                    {active && <svg width="8" height="6" viewBox="0 0 8 6" fill="none"><path d="M1 3L3 5L7 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>}
                  </div>
                  {u}
                </button>
              )
            })}
          </div>
          {customUtilities.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {customUtilities.map(u => (
                <span key={u} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-semibold border"
                  style={{ borderColor: tone, background: `${tone}0d`, color: tone }}>
                  {u}
                  <button type="button" onClick={() => removeCustomUtility(u)}
                    className="ml-0.5 rounded-full w-3.5 h-3.5 grid place-items-center text-[10px] cursor-pointer border-0"
                    style={{ background: tone, color: 'white' }}>×</button>
                </span>
              ))}
            </div>
          )}
          <div className="flex gap-2 mt-2">
            <input className={inp + ' flex-1'} type="text" value={customUtilityInput}
              onChange={e => setCustomUtilityInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCustomUtility() } }}
              placeholder={t('submit_listing_page.ph_custom_utility')} />
            <button type="button" onClick={addCustomUtility} disabled={!customUtilityInput.trim()}
              className="px-4 py-2.5 rounded-lg text-[13px] font-semibold border-0 cursor-pointer disabled:opacity-40 transition-opacity"
              style={{ background: tone, color: 'white' }}>{t('submit_listing_page.add')}</button>
          </div>
        </Sec>

        {/* 7 — Media & Location */}
        <Sec n={n()} title={t('submit_listing_page.sec_media')} tone={tone}>
          <div className="space-y-4">
            {/* Photo upload */}
            <div>
              <Lbl>{t('submit_listing_page.lbl_photos')}</Lbl>
              <button type="button" onClick={() => fileInputRef.current?.click()} disabled={uploading}
                className="w-full flex flex-col items-center gap-2 py-7 rounded-xl border-2 border-dashed cursor-pointer transition-colors disabled:opacity-50"
                style={{ borderColor: tone + '55', background: tone + '06' }}>
                <ImagePlus size={22} style={{ color: tone }} />
                <span className="text-[13px] font-semibold" style={{ color: tone }}>
                  {uploading ? t('submit_listing_page.uploading') : t('submit_listing_page.photos_label')}
                </span>
                <span className="text-[11.5px] text-dim">{t('submit_listing_page.photos_sub')}</span>
              </button>
              <input ref={fileInputRef} type="file" accept="image/jpeg,image/jpg,image/png,image/webp,image/heic" multiple className="hidden" onChange={handleFileChange} />
              {uploadedUrls.length > 0 && (
                <div className="mt-3 grid grid-cols-3 sm:grid-cols-4 gap-2">
                  {uploadedUrls.map(url => {
                    const isThumb = thumbnail === url
                    return (
                      <div key={url} className="relative group rounded-xl overflow-hidden border-2 transition-all" style={{ borderColor: isThumb ? tone : 'transparent' }}>
                        <img src={url} alt="" className="w-full h-24 object-cover" />
                        <button type="button" onClick={() => setThumbnail(url)} title="Set as cover"
                          className="absolute top-1.5 left-1.5 rounded-full p-1 transition-colors"
                          style={{ background: isThumb ? tone : 'rgba(0,0,0,0.45)' }}>
                          <Star size={11} fill={isThumb ? 'white' : 'none'} color="white" />
                        </button>
                        <button type="button" onClick={() => removeImage(url)} title="Remove photo"
                          className="absolute top-1.5 right-1.5 rounded-full p-1 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity">
                          <X size={11} color="white" />
                        </button>
                        {isThumb && (
                          <div className="absolute bottom-0 inset-x-0 text-center text-[10px] font-bold text-white py-0.5" style={{ background: tone + 'cc' }}>
                            {t('submit_listing_page.thumbnail')}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {/* Video links */}
            <div>
              <Lbl>{t('submit_listing_page.field_video_links')}</Lbl>
              <div className="space-y-2">
                {form.video_links.map((url, i) => (
                  <div key={i} className="flex gap-2">
                    <input className={inp + ' flex-1'} type="url" value={url}
                      onChange={e => updateVideoLink(i, e.target.value)}
                      placeholder="https://youtube.com/watch?v=…" />
                    <button type="button" onClick={() => removeVideoLink(i)}
                      className="px-3 py-2.5 rounded-lg border border-line bg-white text-dim cursor-pointer hover:text-red-500 hover:border-red-300 transition-colors">
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
              <button type="button" onClick={addVideoLink}
                className="mt-2 flex items-center gap-1.5 text-[13px] font-semibold cursor-pointer border-0 bg-transparent transition-opacity hover:opacity-70"
                style={{ color: tone }}>
                <Plus size={14} /> {t('submit_listing_page.add_video')}
              </button>
            </div>

            <div>
              <Lbl>{t('submit_listing_page.field_tour_3d')}</Lbl>
              <input className={inp} type="url" value={form.tour_3d_url} onChange={e => set('tour_3d_url', e.target.value)} placeholder="https://my.matterport.com/show/…" />
            </div>

            <div>
              <Lbl>{t('submit_listing_page.field_maps_url')}</Lbl>
              <input className={inp} type="url" value={form.maps_url} onChange={e => {
                const url = e.target.value; set('maps_url', url)
                const coords = parseLatLng(url)
                if (coords) { set('latitude', coords.lat); set('longitude', coords.lng) }
              }} placeholder="https://maps.google.com/…" />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <Lbl>{t('submit_listing_page.field_latitude')}</Lbl>
                <input className={inp} type="number" step="any" value={form.latitude} onChange={e => set('latitude', e.target.value)} placeholder="e.g. 18.5816" />
              </div>
              <div>
                <Lbl>{t('submit_listing_page.field_longitude')}</Lbl>
                <input className={inp} type="number" step="any" value={form.longitude} onChange={e => set('longitude', e.target.value)} placeholder="e.g. -68.4068" />
              </div>
            </div>
            <p className="text-[11.5px] text-dim -mt-1">{t('submit_listing_page.coords_hint')}</p>
          </div>
        </Sec>

        {/* 8 — Tags */}
        <Sec n={n()} title={t('submit_listing_page.sec_tags')} tone={tone}>
          <Lbl>{t('submit_listing_page.field_tags')}</Lbl>
          <div className="flex flex-wrap gap-2 mt-1">
            {ALL_TAGS.map(tag => {
              const active = form.tags.includes(tag)
              return (
                <button key={tag} type="button"
                  onClick={() => set('tags', active ? form.tags.filter(x => x !== tag) : [...form.tags, tag])}
                  className="px-4 py-2 rounded-full border text-[13px] font-semibold cursor-pointer transition-all"
                  style={{ borderColor: active ? tone : '#e2e8f0', background: active ? tone : 'white', color: active ? 'white' : '#64748b' }}>
                  {tag}
                </button>
              )
            })}
          </div>
          {customTags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {customTags.map(tag => (
                <span key={tag} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-semibold border"
                  style={{ borderColor: tone, background: `${tone}0d`, color: tone }}>
                  {tag}
                  <button type="button" onClick={() => set('tags', form.tags.filter(x => x !== tag))}
                    className="ml-0.5 rounded-full w-3.5 h-3.5 grid place-items-center text-[10px] cursor-pointer border-0"
                    style={{ background: tone, color: 'white' }}>×</button>
                </span>
              ))}
            </div>
          )}
          <div className="flex gap-2 mt-2">
            <input className={inp + ' flex-1'} type="text" value={customTagInput}
              onChange={e => setCustomTagInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCustomTag() } }}
              placeholder={t('submit_listing_page.add_custom_feature')} maxLength={50} />
            <button type="button" onClick={addCustomTag} disabled={!customTagInput.trim()}
              className="px-4 py-2.5 rounded-lg text-[13px] font-semibold border-0 cursor-pointer disabled:opacity-40 transition-opacity"
              style={{ background: tone, color: 'white' }}>
              {t('submit_listing_page.add')}
            </button>
          </div>
        </Sec>

        <p className="text-[11.5px] text-ink3 leading-relaxed pt-2">
          {t('submit_listing_page.commission_disclaimer')}
        </p>
        <div className="flex gap-3 pt-3 pb-6">
          <button type="button" onClick={goBack}
            className="px-6 py-3 rounded-full border border-line bg-paper text-ink text-[13.5px] font-semibold cursor-pointer">
            {t('submit_listing_page.cancel')}
          </button>
          <button type="submit" disabled={submitting}
            className="px-8 py-3 rounded-full text-white text-[13.5px] font-bold border-0 cursor-pointer disabled:opacity-50 transition-opacity"
            style={{ background: tone }}>
            {submitting
              ? (isEdit ? t('submit_listing_page.saving') : t('submit_listing_page.submitting'))
              : t(isAdmin ? adminAction.button : isEdit ? 'submit_listing_page.save_btn' : 'submit_listing_page.submit_btn')}
          </button>
        </div>
      </form>
    </div>
  )
}
