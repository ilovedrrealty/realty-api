import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, ImagePlus, X, Star, Plus } from 'lucide-react'
import toast from 'react-hot-toast'
import { PhoneInput } from 'react-international-phone'
import 'react-international-phone/style.css'
import { isValidPhoneNumber } from 'libphonenumber-js'
import { submitListing, updateListing, uploadListingImages, uploadAgreementPdf } from '../../api/listings'
import { RoomPhotoTourEditor } from '../../components/listings/RoomPhotoTourEditor'
import type { AdminListing } from '../../api/admin'
import { RichTextEditor } from '../../components/RichTextEditor'
import { TONE } from './shared'
import { REGION_GROUPS } from '../../data/regions'
import { PAYPAL_ENABLED } from '../../lib/features'

const TYPES    = ['villa', 'apartment', 'condo', 'land', 'commercial']
const FEATURES = [
  'Pool', 'Ocean View', 'Beachfront', 'Oceanfront', 'Furnished', 'Beach Access', 'Mountain View',
  'Parking', 'Gym', 'Smart Home', 'Backup Generator', 'Solar Panels',
]
const BASE_TAGS = ['Luxury', 'New', 'Investment', 'Commercial', 'Pet Friendly', 'Short Term', 'Long Term', 'Furnished', 'Ocean View', 'Mountain View']
const INCLUDED_UTILITIES = ['Water', 'Electricity', 'Gas', 'WiFi', 'Cable TV', 'Trash Removal', 'Pool Maintenance', 'Gardening', 'Security', 'Parking', 'Laundry']
const DEPOSIT_OPTIONS = [
  { value: 'first',      label: 'First month' },
  { value: 'last',       label: 'Last month' },
  { value: 'first_last', label: 'First + Last' },
  { value: 'none',       label: 'None' },
]

const inp = 'w-full px-3 py-2.5 rounded-lg border border-line bg-white text-[13.5px] text-ink outline-none transition-colors focus:border-[#0d9488]'

function Sec({ n, title, tone, children }: { n: number; title: string; tone: string; children: React.ReactNode }) {
  return (
    <div className="bg-paper border border-line rounded-2xl px-6 py-5">
      <div className="flex items-center gap-3 mb-5">
        <div className="w-6 h-6 rounded-full text-white text-[11px] font-bold grid place-items-center shrink-0" style={{ background: tone }}>{n}</div>
        <div className="text-[14px] font-bold text-ink">{title}</div>
        <div className="flex-1 h-px bg-line-soft" />
      </div>
      {children}
    </div>
  )
}

function Lbl({ children }: { children: React.ReactNode }) {
  return <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide mb-1.5">{children}</div>
}

function FormToggle({ value, onChange, label, tone }: { value: boolean; onChange: (v: boolean) => void; label: string; tone: string }) {
  return (
    <button type="button" onClick={() => onChange(!value)}
      className="flex items-center gap-3 px-4 py-2.5 rounded-xl border cursor-pointer text-[13px] font-semibold w-full text-left transition-all"
      style={{ borderColor: value ? tone : '#e2e8f0', background: value ? `${tone}0d` : 'white', color: value ? tone : '#64748b' }}>
      <div className="w-9 h-5 rounded-full relative shrink-0 transition-colors duration-150" style={{ background: value ? tone : '#cbd5e1' }}>
        <div className="absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-all duration-150" style={{ left: value ? '1.25rem' : '0.125rem' }} />
      </div>
      {label}
    </button>
  )
}

function PhotoUploader({ tone, uploadedUrls, thumbnail, uploading, fileInputRef, onFileChange, onRemove, onSetThumbnail }: {
  tone: string; uploadedUrls: string[]; thumbnail: string | null; uploading: boolean
  fileInputRef: React.RefObject<HTMLInputElement | null>
  onFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  onRemove: (url: string) => void; onSetThumbnail: (url: string) => void
}) {
  const { t } = useTranslation('admin')
  return (
    <div>
      <Lbl>{t('submit_listing.lbl_property_photos')}</Lbl>
      <button type="button" onClick={() => fileInputRef.current?.click()} disabled={uploading}
        className="w-full flex flex-col items-center gap-2 py-7 rounded-xl border-2 border-dashed cursor-pointer transition-colors disabled:opacity-50"
        style={{ borderColor: tone + '55', background: tone + '06' }}>
        <ImagePlus size={22} style={{ color: tone }} />
        <span className="text-[13px] font-semibold" style={{ color: tone }}>
          {uploading ? t('submit_listing.uploading') : t('submit_listing.upload_cta')}
        </span>
        <span className="text-[11.5px] text-dim">{t('submit_listing.upload_hint')}</span>
      </button>
      <input ref={fileInputRef} type="file" accept="image/jpeg,image/jpg,image/png,image/webp,image/heic" multiple className="hidden" onChange={onFileChange} />
      {uploadedUrls.length > 0 && (
        <div className="mt-3 grid grid-cols-3 sm:grid-cols-4 gap-2">
          {uploadedUrls.map(url => {
            const isThumb = thumbnail === url
            return (
              <div key={url} className="relative group rounded-xl overflow-hidden border-2 transition-all" style={{ borderColor: isThumb ? tone : 'transparent' }}>
                <img src={url} alt="" className="w-full h-24 object-cover" />
                <button type="button" onClick={() => onSetThumbnail(url)} title="Set as thumbnail"
                  className="absolute top-1.5 left-1.5 rounded-full p-1 transition-colors" style={{ background: isThumb ? tone : 'rgba(0,0,0,0.45)' }}>
                  <Star size={11} fill={isThumb ? 'white' : 'none'} color="white" />
                </button>
                <button type="button" onClick={() => onRemove(url)} title="Remove photo"
                  className="absolute top-1.5 right-1.5 rounded-full p-1 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity">
                  <X size={11} color="white" />
                </button>
                {isThumb && (
                  <div className="absolute bottom-0 inset-x-0 text-center text-[10px] font-bold text-white py-0.5" style={{ background: tone + 'cc' }}>
                    {t('submit_listing.thumbnail')}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function parseLatLng(url: string): { lat: string; lng: string } | null {
  const atMatch = url.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/)
  if (atMatch) return { lat: atMatch[1], lng: atMatch[2] }
  const qMatch = url.match(/[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)/)
  if (qMatch) return { lat: qMatch[1], lng: qMatch[2] }
  return null
}

// ── Shared form body ──────────────────────────────────────────────────────────
// Used by both create (AdminSubmitListing) and edit (AdminEditListing)

function AdminFormBody({
  form, set, tone,
  uploadedUrls, thumbnail, uploading, fileInputRef, handleFileChange, removeImage, setThumbnail,
  priceCurrency, setPriceCurrency, hoaFeeCurrency, setHoaFeeCurrency,
  dayRateCurrency, setDayRateCurrency, monthRateCurrency, setMonthRateCurrency,
  assocFeeCurrency, setAssocFeeCurrency, dopRate,
  customInput, setCustomInput, customTagInput, setCustomTagInput, customTags,
  customUtilityInput, setCustomUtilityInput, customUtilities,
  addCustomFeature, addCustomTag, removeCustomTag, toggleFeature,
  addCustomUtility, removeCustomUtility,
  agreementUploading, onAgreementUpload,
}: {
  form: Record<string, unknown>
  set: (f: string, v: unknown) => void
  tone: string
  uploadedUrls: string[]; thumbnail: string | null; uploading: boolean
  fileInputRef: React.RefObject<HTMLInputElement | null>
  handleFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  removeImage: (url: string) => void; setThumbnail: (url: string) => void
  priceCurrency: 'USD' | 'DOP'; setPriceCurrency: (c: 'USD' | 'DOP') => void
  hoaFeeCurrency: 'USD' | 'DOP'; setHoaFeeCurrency: (c: 'USD' | 'DOP') => void
  dayRateCurrency: 'USD' | 'DOP'; setDayRateCurrency: (c: 'USD' | 'DOP') => void
  monthRateCurrency: 'USD' | 'DOP'; setMonthRateCurrency: (c: 'USD' | 'DOP') => void
  assocFeeCurrency: 'USD' | 'DOP'; setAssocFeeCurrency: (c: 'USD' | 'DOP') => void
  dopRate: number
  customInput: string; setCustomInput: (v: string) => void
  customTagInput: string; setCustomTagInput: (v: string) => void
  customTags: string[]
  customUtilityInput: string; setCustomUtilityInput: (v: string) => void
  customUtilities: string[]
  addCustomFeature: () => void; addCustomTag: () => void; removeCustomTag: (tag: string) => void
  toggleFeature: (f: string) => void
  addCustomUtility: () => void; removeCustomUtility: (u: string) => void
  agreementUploading: boolean
  onAgreementUpload: (file: File) => Promise<void>
}) {
  const { t } = useTranslation('admin')
  const [descLang, setDescLang] = useState<'en' | 'es'>('en')
  const features          = form.features as string[]
  const tags              = form.tags as string[]
  const videoLinks        = form.video_links as string[]
  const includedUtilities = form.included_utilities as string[]
  const isRent            = form.transaction === 'rent'
  const hoa               = form.hoa as boolean
  const association       = form.association as boolean

  const allTagOptions = [...BASE_TAGS, ...customTags.filter(tag => !BASE_TAGS.includes(tag))]

  function toggleTag(tag: string) {
    set('tags', tags.includes(tag) ? tags.filter(x => x !== tag) : [...tags, tag])
  }

  function toggleIncluded(u: string) {
    set('included_utilities', includedUtilities.includes(u)
      ? includedUtilities.filter(x => x !== u)
      : [...includedUtilities, u])
  }

  function addVideoLink() { set('video_links', [...videoLinks, '']) }

  function updateVideoLink(i: number, val: string) {
    const arr = [...videoLinks]; arr[i] = val; set('video_links', arr)
  }

  function removeVideoLink(i: number) {
    set('video_links', videoLinks.filter((_, j) => j !== i))
  }

  function handleCurrencyToggle(newCurrency: 'USD' | 'DOP') {
    const raw = parseFloat((form.price as string) || '0') || 0
    if (newCurrency === 'DOP' && priceCurrency === 'USD') set('price', Math.round(raw * dopRate).toString())
    else if (newCurrency === 'USD' && priceCurrency === 'DOP') set('price', Math.round(raw / dopRate).toString())
    setPriceCurrency(newCurrency)
  }

  function handleHoaFeeCurrencyToggle(newCurrency: 'USD' | 'DOP') {
    const raw = parseFloat((form.hoa_fee as string) || '0') || 0
    if (newCurrency === 'DOP' && hoaFeeCurrency === 'USD') set('hoa_fee', Math.round(raw * dopRate).toString())
    else if (newCurrency === 'USD' && hoaFeeCurrency === 'DOP') set('hoa_fee', Math.round(raw / dopRate).toString())
    setHoaFeeCurrency(newCurrency)
  }

  function handleDayRateCurrencyToggle(newCurrency: 'USD' | 'DOP') {
    const raw = parseFloat((form.price_per_day as string) || '0') || 0
    if (newCurrency === 'DOP' && dayRateCurrency === 'USD') set('price_per_day', Math.round(raw * dopRate).toString())
    else if (newCurrency === 'USD' && dayRateCurrency === 'DOP') set('price_per_day', Math.round(raw / dopRate).toString())
    setDayRateCurrency(newCurrency)
  }

  function handleMonthRateCurrencyToggle(newCurrency: 'USD' | 'DOP') {
    const raw = parseFloat((form.price_per_month as string) || '0') || 0
    if (newCurrency === 'DOP' && monthRateCurrency === 'USD') set('price_per_month', Math.round(raw * dopRate).toString())
    else if (newCurrency === 'USD' && monthRateCurrency === 'DOP') set('price_per_month', Math.round(raw / dopRate).toString())
    setMonthRateCurrency(newCurrency)
  }

  function handleAssocFeeCurrencyToggle(newCurrency: 'USD' | 'DOP') {
    const raw = parseFloat((form.association_fee as string) || '0') || 0
    if (newCurrency === 'DOP' && assocFeeCurrency === 'USD') set('association_fee', Math.round(raw * dopRate).toString())
    else if (newCurrency === 'USD' && assocFeeCurrency === 'DOP') set('association_fee', Math.round(raw / dopRate).toString())
    setAssocFeeCurrency(newCurrency)
  }

  const featureKey = (f: string) => `submit_listing.feature_${f.toLowerCase().replace(/ /g, '_')}`
  const tagKey     = (tag: string) => `submit_listing.tag_${tag.toLowerCase().replace(/ /g, '_')}`
  const utilKey    = (u: string) => `submit_listing.util_${u.toLowerCase().replace(/ /g, '_')}`

  let sn = 0
  const n = () => ++sn

  return (
    <>
      {/* 1 — Basic Info */}
      <Sec n={n()} title={t('submit_listing.sec_basic')} tone={tone}>
        <div className="space-y-4">
          <div>
            <Lbl>{t('submit_listing.lbl_property_title')}</Lbl>
            <input className={inp} value={form.title as string} onChange={e => set('title', e.target.value)} placeholder="e.g. Oceanfront Villa with Infinity Pool" required />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Lbl>{t('submit_listing.lbl_property_type')}</Lbl>
              <select className={inp + ' cursor-pointer'} value={form.type as string} onChange={e => set('type', e.target.value)}>
                {TYPES.map(tp => <option key={tp} value={tp}>{t(`submit_listing.type_${tp}`, { defaultValue: tp.charAt(0).toUpperCase() + tp.slice(1) })}</option>)}
              </select>
            </div>
            <div>
              <Lbl>{t('submit_listing.lbl_purpose')}</Lbl>
              <div className="flex gap-2">
                {(['sale', 'rent'] as const).map(p => (
                  <button key={p} type="button" onClick={() => set('transaction', p)}
                    className="flex-1 py-2.5 rounded-lg text-[13px] font-semibold border cursor-pointer transition-all"
                    style={{ background: form.transaction === p ? tone : 'white', color: form.transaction === p ? 'white' : '#64748b', borderColor: form.transaction === p ? tone : '#e2e8f0' }}>
                    {p === 'sale' ? t('submit_listing.for_sale') : t('submit_listing.for_rent')}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {!isRent ? (
            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="flex items-center h-7 mb-1.5">
                  <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide">{t('submit_listing.lbl_region')}</div>
                </div>
                <select className={inp + ' cursor-pointer'} value={form.location as string} onChange={e => set('location', e.target.value)} required>
                  <option value="">{t('submit_listing.select_region')}</option>
                  {REGION_GROUPS.map(g => (
                    <optgroup key={g.key} label={t(`submit_listing.region_group_${g.key}`)}>
                      {g.regions.map(r => <option key={r} value={r}>{r}</option>)}
                    </optgroup>
                  ))}
                </select>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide">{t('submit_listing.lbl_price')}</div>
                  <div className="flex rounded-lg border border-line overflow-hidden text-[11px] font-bold">
                    {(['USD', 'DOP'] as const).map(c => (
                      <button key={c} type="button" onClick={() => handleCurrencyToggle(c)} className="px-2.5 py-1 transition-colors cursor-pointer"
                        style={{ background: priceCurrency === c ? tone : 'white', color: priceCurrency === c ? 'white' : '#64748b' }}>{c}</button>
                    ))}
                  </div>
                </div>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-dim text-[13px]">{priceCurrency === 'USD' ? '$' : 'RD$'}</span>
                  <input className={inp + (priceCurrency === 'USD' ? ' pl-6' : ' pl-11')} type="text" inputMode="numeric"
                    value={(form.price as string) ? Number(form.price as string).toLocaleString('en-US') : ''}
                    onChange={e => set('price', e.target.value.replace(/[^0-9]/g, ''))}
                    placeholder={priceCurrency === 'USD' ? 'e.g. 850,000' : 'e.g. 50,150,000'} required />
                </div>
                {(form.price as string) ? (
                  <p className="text-[11.5px] text-dim mt-1">
                    {priceCurrency === 'USD'
                      ? t('submit_listing.price_hint_dop', { amount: Math.round(parseFloat(form.price as string) * dopRate).toLocaleString('en-US') })
                      : t('submit_listing.price_hint_usd_rate', { amount: Math.round(parseFloat(form.price as string) / dopRate).toLocaleString('en-US'), rate: dopRate.toFixed(1) })}
                  </p>
                ) : null}
              </div>
            </div>
          ) : (
            <div>
              <div className="flex items-center h-7 mb-1.5">
                <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide">{t('submit_listing.lbl_region')}</div>
              </div>
              <select className={inp + ' cursor-pointer'} value={form.location as string} onChange={e => set('location', e.target.value)} required>
                <option value="">{t('submit_listing.select_region')}</option>
                {REGION_GROUPS.map(g => (
                  <optgroup key={g.key} label={t(`submit_listing.region_group_${g.key}`)}>
                    {g.regions.map(r => <option key={r} value={r}>{r}</option>)}
                  </optgroup>
                ))}
              </select>
            </div>
          )}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <Lbl>{t('submit_listing.lbl_description')}</Lbl>
              <div className="flex items-center gap-0.5 rounded-lg border border-line p-0.5">
                {(['en', 'es'] as const).map(lang => (
                  <button key={lang} type="button" onClick={() => setDescLang(lang)}
                    className="px-2.5 py-1 rounded-md text-[11px] font-bold uppercase tracking-wide transition-colors"
                    style={{ background: descLang === lang ? tone : 'transparent', color: descLang === lang ? 'white' : '#64748b' }}>
                    {lang}
                  </button>
                ))}
              </div>
            </div>
            {descLang === 'en' ? (
              <RichTextEditor value={form.description as string} onChange={v => set('description', v)} tone={tone} />
            ) : (
              <RichTextEditor value={(form.description_es as string) ?? ''} onChange={v => set('description_es', v)} tone={tone} />
            )}
          </div>
        </div>
      </Sec>

      {/* 2 — Property Details */}
      <Sec n={n()} title={t('submit_listing.sec_details')} tone={tone}>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            { key: 'bedrooms',      lbl: t('submit_listing.lbl_bedrooms'),  ph: '3',    step: '1'   },
            { key: 'bathrooms',     lbl: t('submit_listing.lbl_bathrooms'), ph: '1.5',  step: '0.5' },
            { key: 'area_sqft',     lbl: t('submit_listing.lbl_area'),      ph: '2400', step: '1'   },
            { key: 'lot_size_sqft', lbl: t('submit_listing.lbl_lot'),       ph: '5000', step: '1'   },
          ].map(({ key, lbl, ph, step }) => (
            <div key={key}>
              <Lbl>{lbl}</Lbl>
              <input className={inp} type="number" step={step} value={form[key] as string} onChange={e => set(key, e.target.value)} placeholder={ph} min="0" />
            </div>
          ))}
        </div>
      </Sec>

      {/* 3 — Construction */}
      <Sec n={n()} title={t('submit_listing.sec_construction')} tone={tone}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <Lbl>{t('submit_listing.lbl_construction_status')}</Lbl>
            <select className={inp + ' cursor-pointer'} value={form.construction_status as string} onChange={e => set('construction_status', e.target.value)}>
              <option value="">{t('submit_listing.construction_none')}</option>
              <option value="pre_construction">{t('submit_listing.construction_pre')}</option>
              <option value="under_construction">{t('submit_listing.construction_under')}</option>
              <option value="completed">{t('submit_listing.construction_done')}</option>
            </select>
          </div>
          {form.construction_status === 'completed' && (
            <div>
              <Lbl>{t('submit_listing.lbl_year_built')}</Lbl>
              <input className={inp} type="number" value={form.year_built as string} onChange={e => set('year_built', e.target.value)} placeholder="e.g. 2019" min="1900" max={new Date().getFullYear()} />
            </div>
          )}
        </div>
      </Sec>

      {/* 4 — Financials (dynamic based on sale/rent) */}
      <Sec n={n()} title={t('submit_listing.sec_financials')} tone={tone}>
        {isRent ? (
          <div className="space-y-4">
            <p className="text-[12px] text-dim -mt-1">{t('submit_listing.rent_price_hint')}</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide">Short-Term Rate (per night)</div>
                  <div className="flex rounded-lg border border-line overflow-hidden text-[11px] font-bold">
                    {(['USD', 'DOP'] as const).map(c => (
                      <button key={c} type="button" onClick={() => handleDayRateCurrencyToggle(c)} className="px-2.5 py-1 transition-colors cursor-pointer"
                        style={{ background: dayRateCurrency === c ? tone : 'white', color: dayRateCurrency === c ? 'white' : '#64748b' }}>{c}</button>
                    ))}
                  </div>
                </div>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-dim text-[13px]">{dayRateCurrency === 'USD' ? '$' : 'RD$'}</span>
                  <input className={inp + (dayRateCurrency === 'USD' ? ' pl-6' : ' pl-11')} type="text" inputMode="numeric"
                    value={(form.price_per_day as string) ? Number(form.price_per_day).toLocaleString('en-US') : ''}
                    onChange={e => set('price_per_day', e.target.value.replace(/[^0-9]/g, ''))}
                    placeholder={dayRateCurrency === 'USD' ? 'e.g. 150' : 'e.g. 8,900'} />
                </div>
                <p className="text-[11.5px] text-dim mt-1">
                  {(form.price_per_day as string) && parseFloat(form.price_per_day as string) > 0
                    ? dayRateCurrency === 'USD'
                      ? `≈ DOP ${Math.round(parseFloat(form.price_per_day as string) * dopRate).toLocaleString('en-US')}`
                      : `≈ USD ${Math.round(parseFloat(form.price_per_day as string) / dopRate).toLocaleString('en-US')}`
                    : 'Daily rate for short-term, vacation rentals'}
                </p>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide">Mid-, Long-Term Rate (per month)</div>
                  <div className="flex rounded-lg border border-line overflow-hidden text-[11px] font-bold">
                    {(['USD', 'DOP'] as const).map(c => (
                      <button key={c} type="button" onClick={() => handleMonthRateCurrencyToggle(c)} className="px-2.5 py-1 transition-colors cursor-pointer"
                        style={{ background: monthRateCurrency === c ? tone : 'white', color: monthRateCurrency === c ? 'white' : '#64748b' }}>{c}</button>
                    ))}
                  </div>
                </div>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-dim text-[13px]">{monthRateCurrency === 'USD' ? '$' : 'RD$'}</span>
                  <input className={inp + (monthRateCurrency === 'USD' ? ' pl-6' : ' pl-11')} type="text" inputMode="numeric"
                    value={(form.price_per_month as string) ? Number(form.price_per_month).toLocaleString('en-US') : ''}
                    onChange={e => set('price_per_month', e.target.value.replace(/[^0-9]/g, ''))}
                    placeholder={monthRateCurrency === 'USD' ? 'e.g. 2,500' : 'e.g. 148,000'} />
                </div>
                <p className="text-[11.5px] text-dim mt-1">
                  {(form.price_per_month as string) && parseFloat(form.price_per_month as string) > 0
                    ? monthRateCurrency === 'USD'
                      ? `≈ DOP ${Math.round(parseFloat(form.price_per_month as string) * dopRate).toLocaleString('en-US')}`
                      : `≈ USD ${Math.round(parseFloat(form.price_per_month as string) / dopRate).toLocaleString('en-US')}`
                    : 'Monthly rate for 30-day+ rentals'}
                </p>
              </div>
            </div>
            {PAYPAL_ENABLED && (form.price_per_day as string) && (
              <div>
                <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide mb-1.5">
                  Owner PayPal Email <span className="text-red-500">*</span>
                </div>
                <input
                  className={inp}
                  type="email"
                  value={(form.owner_paypal_email as string) ?? ''}
                  onChange={e => set('owner_paypal_email', e.target.value.trim())}
                  placeholder="owner@example.com"
                />
                <p className="text-[11.5px] text-dim mt-1">
                  Payout for this listing's bookings will be sent to this PayPal account (after the 20% platform fee).
                </p>
              </div>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <FormToggle value={association} onChange={v => { set('association', v); if (!v) set('association_fee', '') }} label={t('submit_listing.toggle_assoc_fee')} tone={tone} />
              {association && (
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide">{t('submit_listing.lbl_assoc_fee')}</div>
                    <div className="flex rounded-lg border border-line overflow-hidden text-[11px] font-bold">
                      {(['USD', 'DOP'] as const).map(c => (
                        <button key={c} type="button" onClick={() => handleAssocFeeCurrencyToggle(c)} className="px-2.5 py-1 transition-colors cursor-pointer"
                          style={{ background: assocFeeCurrency === c ? tone : 'white', color: assocFeeCurrency === c ? 'white' : '#64748b' }}>{c}</button>
                      ))}
                    </div>
                  </div>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-dim text-[13px]">{assocFeeCurrency === 'USD' ? '$' : 'RD$'}</span>
                    <input className={inp + (assocFeeCurrency === 'USD' ? ' pl-6' : ' pl-11')} type="text" inputMode="numeric"
                      value={(form.association_fee as string) ? Number(form.association_fee).toLocaleString('en-US') : ''}
                      onChange={e => set('association_fee', e.target.value.replace(/[^0-9]/g, ''))}
                      placeholder={assocFeeCurrency === 'USD' ? 'e.g. 200' : 'e.g. 11,900'} />
                  </div>
                  <p className="text-[11.5px] text-dim mt-1">
                    {(form.association_fee as string) && parseFloat(form.association_fee as string) > 0
                      ? assocFeeCurrency === 'USD'
                        ? `≈ DOP ${Math.round(parseFloat(form.association_fee as string) * dopRate).toLocaleString('en-US')}`
                        : `≈ USD ${Math.round(parseFloat(form.association_fee as string) / dopRate).toLocaleString('en-US')}`
                      : 'Monthly association fee'}
                  </p>
                </div>
              )}
            </div>
            <div>
              <Lbl>{t('submit_listing.lbl_deposit')}</Lbl>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-1">
                {DEPOSIT_OPTIONS.map(opt => (
                  <button key={opt.value} type="button"
                    onClick={() => set('deposit_policy', form.deposit_policy === opt.value ? '' : opt.value)}
                    className="py-2.5 rounded-lg text-[13px] font-semibold border cursor-pointer transition-all"
                    style={{ background: form.deposit_policy === opt.value ? tone : 'white', color: form.deposit_policy === opt.value ? 'white' : '#64748b', borderColor: form.deposit_policy === opt.value ? tone : '#e2e8f0' }}>
                    {t(`submit_listing.deposit_${opt.value}`, { defaultValue: opt.label })}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <Lbl>{t('submit_listing.lbl_roi')}</Lbl>
                <input className={inp} type="number" step="0.1" value={form.roi as string} onChange={e => set('roi', e.target.value)} placeholder="e.g. 8.5" min="0" max="100" />
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <FormToggle value={form.seller_financing as boolean} onChange={v => set('seller_financing', v)} label={t('submit_listing.toggle_seller_fin')} tone={tone} />
              <FormToggle value={form.tax_exempt as boolean} onChange={v => set('tax_exempt', v)} label={t('submit_listing.toggle_tax_exempt')} tone={tone} />
              <FormToggle value={hoa} onChange={v => { set('hoa', v); if (!v) { set('hoa_fee', ''); setHoaFeeCurrency('USD') } }} label={t('submit_listing.toggle_hoa')} tone={tone} />
              {hoa && (
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="text-[11.5px] font-semibold text-dim uppercase tracking-wide">{t('submit_listing.lbl_hoa_fee')}</div>
                    <div className="flex rounded-lg border border-line overflow-hidden text-[11px] font-bold">
                      {(['USD', 'DOP'] as const).map(c => (
                        <button key={c} type="button" onClick={() => handleHoaFeeCurrencyToggle(c)} className="px-2.5 py-1 transition-colors cursor-pointer"
                          style={{ background: hoaFeeCurrency === c ? tone : 'white', color: hoaFeeCurrency === c ? 'white' : '#64748b' }}>{c}</button>
                      ))}
                    </div>
                  </div>
                  <input className={inp} type="text" inputMode="numeric"
                    value={(form.hoa_fee as string) ? Number(form.hoa_fee as string).toLocaleString('en-US') : ''}
                    onChange={e => set('hoa_fee', e.target.value.replace(/[^0-9]/g, ''))}
                    placeholder={hoaFeeCurrency === 'USD' ? 'e.g. 350' : 'e.g. 20,650'} />
                  {(form.hoa_fee as string) ? (
                    <p className="text-[11.5px] text-dim mt-1">
                      {hoaFeeCurrency === 'USD'
                        ? t('submit_listing.price_hint_dop', { amount: Math.round(parseFloat(form.hoa_fee as string) * dopRate).toLocaleString('en-US') })
                        : t('submit_listing.price_hint_usd', { amount: Math.round(parseFloat(form.hoa_fee as string) / dopRate).toLocaleString('en-US') })}
                    </p>
                  ) : null}
                </div>
              )}
            </div>
          </div>
        )}
      </Sec>

      {/* 5 — Community & Features */}
      <Sec n={n()} title={t('submit_listing.sec_features')} tone={tone}>
        <div className="space-y-4">
          <FormToggle value={form.gated_community as boolean} onChange={v => set('gated_community', v)} label={t('submit_listing.toggle_gated')} tone={tone} />
          <div>
            <Lbl>{t('submit_listing.lbl_property_features')}</Lbl>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mt-1">
              {FEATURES.map(f => {
                const active = features.includes(f)
                return (
                  <button key={f} type="button" onClick={() => toggleFeature(f)}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg border text-[13px] cursor-pointer transition-all text-left"
                    style={{ borderColor: active ? tone : '#e2e8f0', background: active ? `${tone}0d` : 'white', color: active ? tone : '#64748b', fontWeight: active ? 600 : 400 }}>
                    <div className="w-3.5 h-3.5 rounded border shrink-0 grid place-items-center transition-colors" style={{ borderColor: active ? tone : '#cbd5e1', background: active ? tone : 'white' }}>
                      {active && <svg width="8" height="6" viewBox="0 0 8 6" fill="none"><path d="M1 3L3 5L7 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>}
                    </div>
                    {t(featureKey(f), { defaultValue: f })}
                  </button>
                )
              })}
            </div>
            {features.filter(f => !FEATURES.includes(f)).length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {features.filter(f => !FEATURES.includes(f)).map(f => (
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
                placeholder={t('submit_listing.ph_feature')} />
              <button type="button" onClick={addCustomFeature} disabled={!customInput.trim()}
                className="px-4 py-2.5 rounded-lg text-[13px] font-semibold border-0 cursor-pointer disabled:opacity-40 transition-opacity"
                style={{ background: tone, color: 'white' }}>{t('submit_listing.add')}</button>
            </div>
          </div>
        </div>
      </Sec>

      {/* 6 — What is Included (rent only) */}
      {isRent && (
        <Sec n={n()} title={t('submit_listing.sec_included')} tone={tone}>
          <p className="text-[12.5px] text-dim mb-3">{t('submit_listing.included_hint')}</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {INCLUDED_UTILITIES.map(u => {
              const active = includedUtilities.includes(u)
              return (
                <button key={u} type="button" onClick={() => toggleIncluded(u)}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg border text-[13px] cursor-pointer transition-all text-left"
                  style={{ borderColor: active ? tone : '#e2e8f0', background: active ? `${tone}0d` : 'white', color: active ? tone : '#64748b', fontWeight: active ? 600 : 400 }}>
                  <div className="w-3.5 h-3.5 rounded border shrink-0 grid place-items-center transition-colors" style={{ borderColor: active ? tone : '#cbd5e1', background: active ? tone : 'white' }}>
                    {active && <svg width="8" height="6" viewBox="0 0 8 6" fill="none"><path d="M1 3L3 5L7 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>}
                  </div>
                  {t(utilKey(u), { defaultValue: u })}
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
              placeholder={t('submit_listing.ph_utility')} />
            <button type="button" onClick={addCustomUtility} disabled={!customUtilityInput.trim()}
              className="px-4 py-2.5 rounded-lg text-[13px] font-semibold border-0 cursor-pointer disabled:opacity-40 transition-opacity"
              style={{ background: tone, color: 'white' }}>{t('submit_listing.add')}</button>
          </div>
        </Sec>
      )}

      {/* 7 — Media & Location */}
      <Sec n={n()} title={t('submit_listing.sec_media')} tone={tone}>
        <div className="space-y-4">
          <PhotoUploader
            tone={tone} uploadedUrls={uploadedUrls} thumbnail={thumbnail} uploading={uploading}
            fileInputRef={fileInputRef} onFileChange={handleFileChange} onRemove={removeImage} onSetThumbnail={setThumbnail}
          />

          {/* Video links */}
          <div>
            <Lbl>{t('submit_listing.lbl_video_links')}</Lbl>
            <div className="space-y-2">
              {videoLinks.map((url, i) => (
                <div key={i} className="flex gap-2">
                  <input className={inp + ' flex-1'} type="url" value={url}
                    onChange={e => updateVideoLink(i, e.target.value)} placeholder="https://youtube.com/watch?v=…" />
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
              <Plus size={14} /> {t('submit_listing.add_video')}
            </button>
          </div>

          {/* 3D Tour */}
          <div>
            <Lbl>{t('submit_listing.lbl_3d_tour')}</Lbl>
            <input className={inp} type="url" value={form.tour_3d_url as string} onChange={e => set('tour_3d_url', e.target.value)} placeholder="https://my.matterport.com/show/…" />
          </div>

          <div>
            <Lbl>{t('submit_listing.lbl_maps_url')}</Lbl>
            <input className={inp} type="url" value={form.maps_url as string} onChange={e => {
              const url = e.target.value; set('maps_url', url)
              const coords = parseLatLng(url)
              if (coords) { set('latitude', coords.lat); set('longitude', coords.lng) }
            }} placeholder="https://maps.google.com/…" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Lbl>{t('submit_listing.lbl_latitude')}</Lbl>
              <input className={inp} type="number" step="any" value={form.latitude as string} onChange={e => set('latitude', e.target.value)} placeholder="e.g. 18.5816" />
            </div>
            <div>
              <Lbl>{t('submit_listing.lbl_longitude')}</Lbl>
              <input className={inp} type="number" step="any" value={form.longitude as string} onChange={e => set('longitude', e.target.value)} placeholder="e.g. -68.4068" />
            </div>
          </div>
          <p className="text-[11.5px] text-dim -mt-1">{t('submit_listing.maps_hint')}</p>
        </div>
      </Sec>

      {/* 8 — Utilities */}
      <Sec n={n()} title={t('submit_listing.sec_utilities')} tone={tone}>
        <Lbl>{t('submit_listing.lbl_utility_notes')}</Lbl>
        <RichTextEditor value={form.utilities as string} onChange={v => set('utilities', v)} tone={tone} />
        <p className="text-[11.5px] text-dim mt-1">{t('submit_listing.utilities_hint')}</p>
      </Sec>

      {/* 9 — Co-Listing */}
      <Sec n={n()} title={t('submit_listing.sec_colisting')} tone={tone}>
        <div className="space-y-4">
          <FormToggle
            value={form.co_listing_enabled as boolean}
            onChange={v => {
              set('co_listing_enabled', v)
              if (!v) {
                set('co_listing_brokerage', '')
                set('co_listing_agent_name', '')
                set('co_listing_brokerage_email', '')
                set('co_listing_brokerage_phone', '')
                set('co_listing_commission_split', '')
                set('co_listing_notes', '')
                set('co_listing_status', '')
              }
            }}
            label={t('submit_listing.toggle_colisting')}
            tone={tone}
          />
          {(form.co_listing_enabled as boolean) && (
            <div className="space-y-4 pt-1">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <Lbl>{t('submit_listing.lbl_ext_brokerage')}</Lbl>
                  <input className={inp} value={form.co_listing_brokerage as string} onChange={e => set('co_listing_brokerage', e.target.value)} placeholder="e.g. Century 21 DR" />
                </div>
                <div>
                  <Lbl>{t('submit_listing.lbl_ext_agent')}</Lbl>
                  <input className={inp} value={form.co_listing_agent_name as string} onChange={e => set('co_listing_agent_name', e.target.value)} placeholder="e.g. María López" />
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <Lbl>Brokerage Email</Lbl>
                  <input className={inp} type="email" value={form.co_listing_brokerage_email as string} onChange={e => set('co_listing_brokerage_email', e.target.value)} placeholder="e.g. contact@century21dr.com" />
                </div>
                <div>
                  <Lbl>{t('submit_listing.lbl_commission')}</Lbl>
                  <input className={inp} type="number" step="1" min="0" max="100" value={form.co_listing_commission_split as string} onChange={e => set('co_listing_commission_split', e.target.value)} placeholder="e.g. 50" />
                </div>
              </div>
              <div>
                <Lbl>Brokerage Phone</Lbl>
                <PhoneInput
                  defaultCountry="do"
                  value={form.co_listing_brokerage_phone as string}
                  onChange={phone => set('co_listing_brokerage_phone', phone)}
                  inputClassName="!w-full !px-3 !py-2.5 !rounded-r-lg !border-line !bg-white !text-[13.5px] !text-ink !outline-none focus:!border-[#0d9488] !h-auto"
                  countrySelectorStyleProps={{ buttonClassName: '!border-line !bg-white !rounded-l-lg !h-auto !px-2.5 !py-2.5' }}
                  style={{ '--react-international-phone-border-radius': '0.5rem' } as React.CSSProperties}
                />
              </div>
              <div>
                <Lbl>{t('submit_listing.lbl_co_status')}</Lbl>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {CO_LISTING_STATUSES.map(s => (
                    <button
                      key={s.value}
                      type="button"
                      onClick={() => set('co_listing_status', form.co_listing_status === s.value ? '' : s.value)}
                      className="py-2.5 px-3 rounded-lg text-[12.5px] font-semibold border cursor-pointer transition-all text-center"
                      style={{
                        background: form.co_listing_status === s.value ? tone : 'white',
                        color: form.co_listing_status === s.value ? 'white' : '#64748b',
                        borderColor: form.co_listing_status === s.value ? tone : '#e2e8f0',
                      }}
                    >
                      {t(`submit_listing.co_status_${s.value}`, { defaultValue: s.label })}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <Lbl>{t('submit_listing.lbl_co_notes')}</Lbl>
                <textarea
                  className={inp + ' resize-none'}
                  rows={3}
                  value={form.co_listing_notes as string}
                  onChange={e => set('co_listing_notes', e.target.value)}
                  placeholder="e.g. Split agreed verbally, MLS #DR-204, contract expires Dec 2025…"
                />
              </div>
              {/* Terms checkbox */}
              <div>
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={form.co_listing_agreement_accepted as boolean}
                    onChange={e => set('co_listing_agreement_accepted', e.target.checked)}
                    className="mt-0.5 w-4 h-4 rounded cursor-pointer shrink-0"
                    style={{ accentColor: tone }}
                  />
                  <span className="text-[13px] text-ink">
                    I agree to the <strong>co-listing terms</strong> with I Love DR Realty and confirm that the information above is accurate. <span className="text-red-500">*</span>
                  </span>
                </label>
              </div>
              {/* Agreement PDF upload */}
              <div>
                <Lbl>Co-Listing Agreement (PDF)</Lbl>
                {form.co_listing_agreement_url ? (
                  <div className="flex items-center gap-3 p-3 rounded-xl border border-line bg-white">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-red-500 shrink-0"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                    <a href={form.co_listing_agreement_url as string} target="_blank" rel="noopener noreferrer" className="text-[13px] text-blue-600 underline flex-1 truncate">View Uploaded Agreement</a>
                    <button type="button" onClick={() => set('co_listing_agreement_url', '')} className="text-dim hover:text-red-500 cursor-pointer bg-transparent border-0 text-[12px]">Remove</button>
                  </div>
                ) : (
                  <label className="w-full flex items-center gap-3 py-3 px-4 rounded-xl border-2 border-dashed cursor-pointer transition-colors"
                    style={{ borderColor: tone + '55', background: tone + '06' }}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color: tone }}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                    <span className="text-[13px] font-semibold" style={{ color: tone }}>
                      {agreementUploading ? 'Uploading…' : 'Upload Agreement PDF'}
                    </span>
                    <input type="file" accept=".pdf,application/pdf" className="hidden"
                      disabled={agreementUploading}
                      onChange={e => { const f = e.target.files?.[0]; if (f) onAgreementUpload(f) }} />
                  </label>
                )}
                <p className="text-[11.5px] text-dim mt-1">Upload the signed co-listing agreement document (PDF, max 10 MB).</p>
              </div>
            </div>
          )}
        </div>
      </Sec>

      {/* 10 — Listing Tags */}
      <Sec n={n()} title={t('submit_listing.sec_tags')} tone={tone}>
        <Lbl>{t('submit_listing.lbl_tags')}</Lbl>
        <div className="flex flex-wrap gap-2 mt-1">
          {allTagOptions.map(tag => {
            const active = tags.includes(tag)
            const isCustom = !BASE_TAGS.includes(tag)
            return (
              <span key={tag} className="inline-flex items-center rounded-full border text-[13px] font-semibold transition-all"
                style={{ borderColor: active ? tone : '#e2e8f0', background: active ? tone : 'white', color: active ? 'white' : '#64748b' }}>
                <button type="button" onClick={() => toggleTag(tag)} className="pl-4 pr-2 py-2 cursor-pointer bg-transparent border-0 font-semibold text-[13px]" style={{ color: 'inherit' }}>
                  {isCustom ? tag : t(tagKey(tag), { defaultValue: tag })}
                </button>
                {isCustom && (
                  <button type="button" onClick={() => { removeCustomTag(tag); set('tags', tags.filter(x => x !== tag)) }}
                    className="pr-3 py-2 cursor-pointer bg-transparent border-0 text-[11px] leading-none" style={{ color: 'inherit' }}>×</button>
                )}
                {!isCustom && <span className="pr-4" />}
              </span>
            )
          })}
        </div>
        <div className="flex gap-2 mt-2">
          <input className={inp + ' flex-1'} type="text" value={customTagInput}
            onChange={e => setCustomTagInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCustomTag() } }}
            placeholder={t('submit_listing.add_custom_tag')} />
          <button type="button" onClick={addCustomTag} disabled={!customTagInput.trim()}
            className="px-4 py-2.5 rounded-lg text-[13px] font-semibold border-0 cursor-pointer disabled:opacity-40 transition-opacity"
            style={{ background: tone, color: 'white' }}>{t('submit_listing.add')}</button>
        </div>
      </Sec>
    </>
  )
}

// ── Create new listing ────────────────────────────────────────────────────────

const CO_LISTING_STATUSES = [
  { value: 'available', label: 'Available for Co-Listing' },
  { value: 'active',    label: 'Co-Listing Active' },
  { value: 'closed',    label: 'Closed' },
  { value: 'cancelled', label: 'Cancelled' },
]

const EMPTY_FORM = {
  title: '', type: 'villa', transaction: 'sale', location: '', price: '',
  description: '', description_es: '', bedrooms: '', bathrooms: '', area_sqft: '', lot_size_sqft: '',
  construction_status: '', year_built: '', roi: '',
  seller_financing: false, hoa: false, hoa_fee: '', tax_exempt: false, gated_community: false,
  features: [] as string[], maps_url: '', latitude: '', longitude: '',
  tags: [] as string[],
  video_links: [] as string[],
  tour_3d_url: '',
  utilities: '',
  included_utilities: [] as string[],
  association: false,
  association_fee: '',
  deposit_policy: '',
  co_listing_enabled: false,
  co_listing_brokerage: '',
  co_listing_agent_name: '',
  co_listing_brokerage_email: '',
  co_listing_brokerage_phone: '',
  co_listing_commission_split: '',
  co_listing_notes: '',
  co_listing_status: '',
  price_per_day: '',
  price_per_month: '',
  owner_paypal_email: '',
  co_listing_agreement_accepted: false,
  co_listing_agreement_url: '',
}

export function AdminSubmitListing({ go, tone }: { go: (v: string) => void; tone: string }) {
  const { t } = useTranslation('admin')
  const [form, setForm] = useState(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [uploadedUrls, setUploadedUrls] = useState<string[]>([])
  const [thumbnail, setThumbnail] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [priceCurrency, setPriceCurrency] = useState<'USD' | 'DOP'>('USD')
  const [hoaFeeCurrency, setHoaFeeCurrency] = useState<'USD' | 'DOP'>('USD')
  const [dayRateCurrency, setDayRateCurrency] = useState<'USD' | 'DOP'>('USD')
  const [monthRateCurrency, setMonthRateCurrency] = useState<'USD' | 'DOP'>('USD')
  const [assocFeeCurrency, setAssocFeeCurrency] = useState<'USD' | 'DOP'>('USD')
  const [dopRate, setDopRate] = useState(59.5)
  const [customInput, setCustomInput] = useState('')
  const [customTagInput, setCustomTagInput] = useState('')
  const [customTags, setCustomTags] = useState<string[]>([])
  const [customUtilityInput, setCustomUtilityInput] = useState('')
  const [customUtilities, setCustomUtilities] = useState<string[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [agreementUploading, setAgreementUploading] = useState(false)

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

  function addCustomFeature() {
    const toTitle = (s: string) => s.trim().toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    const entries = customInput.split(',').map(toTitle).filter(s => s && !form.features.includes(s))
    if (entries.length) set('features', [...form.features, ...entries])
    setCustomInput('')
  }

  function addCustomTag() {
    const val = customTagInput.trim().toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    if (!val || customTags.includes(val) || BASE_TAGS.includes(val)) return
    setCustomTags(prev => [...prev, val])
    set('tags', [...form.tags, val])
    setCustomTagInput('')
  }

  function removeCustomTag(tag: string) {
    setCustomTags(prev => prev.filter(x => x !== tag))
  }

  function addCustomUtility() {
    const toTitle = (s: string) => s.trim().toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    const entries = customUtilityInput.split(',').map(toTitle).filter(s => s && !INCLUDED_UTILITIES.includes(s) && !customUtilities.includes(s))
    if (!entries.length) return
    setCustomUtilities(prev => [...prev, ...entries])
    set('included_utilities', [...form.included_utilities, ...entries])
    setCustomUtilityInput('')
  }

  function removeCustomUtility(u: string) {
    setCustomUtilities(prev => prev.filter(x => x !== u))
    set('included_utilities', form.included_utilities.filter((x: string) => x !== u))
  }

  async function handleAgreementUpload(file: File) {
    setAgreementUploading(true)
    try {
      const url = await uploadAgreementPdf(file)
      set('co_listing_agreement_url', url)
    } catch {
      toast.error(t('submit_listing.toast_upload_fail'))
    } finally {
      setAgreementUploading(false)
    }
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    if (!files.length) return
    if (uploadedUrls.length + files.length > 25) { toast.error(t('submit_listing.toast_max_photos')); return }
    setUploading(true)
    try {
      const newUrls = await uploadListingImages(files)
      setUploadedUrls(prev => { const merged = [...prev, ...newUrls]; if (!thumbnail) setThumbnail(merged[0]); return merged })
    } catch { toast.error(t('submit_listing.toast_upload_fail')) }
    finally { setUploading(false); if (fileInputRef.current) fileInputRef.current.value = '' }
  }

  function removeImage(url: string) {
    setUploadedUrls(prev => { const next = prev.filter(u => u !== url); if (thumbnail === url) setThumbnail(next[0] ?? null); return next })
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const isRentMode = form.transaction === 'rent' || form.transaction === 'both'
    if (!form.title.trim() || !form.location || (!isRentMode && !form.price)) { toast.error(t('submit_listing.toast_required')); return }
    if (isRentMode && !(form.price_per_day as string) && !(form.price_per_month as string)) { toast.error(t('submit_listing.toast_rent_price_required')); return }
    if (PAYPAL_ENABLED && isRentMode && (form.price_per_day as string) && !(form.owner_paypal_email as string).trim()) { toast.error('Owner PayPal email is required when a daily rate is set.'); return }
    if (form.co_listing_enabled && !form.co_listing_agreement_accepted) { toast.error('You must accept the co-listing terms before submitting.'); return }
    setSubmitting(true)
    try {
      const orderedImages = thumbnail ? [thumbnail, ...uploadedUrls.filter(u => u !== thumbnail)] : uploadedUrls
      const isRent = isRentMode
      const priceUSD = isRent
        ? ((form.price_per_day as string) ? (dayRateCurrency === 'DOP' ? Math.round(parseFloat(form.price_per_day as string) / dopRate) : parseFloat(form.price_per_day as string)) : (monthRateCurrency === 'DOP' ? Math.round(parseFloat(form.price_per_month as string) / dopRate) : parseFloat(form.price_per_month as string)))
        : (priceCurrency === 'DOP' ? Math.round(parseFloat(form.price) / dopRate) : parseFloat(form.price))
      await submitListing({
        title: form.title.trim(), description: form.description || undefined,
        description_es: form.description_es || undefined,
        type: form.type, transaction: form.transaction,
        price: priceUSD, location: form.location,
        bedrooms: form.bedrooms ? parseInt(form.bedrooms) : undefined,
        bathrooms: form.bathrooms ? parseFloat(form.bathrooms) : undefined,
        area_sqft: form.area_sqft ? parseInt(form.area_sqft) : undefined,
        lot_size_sqft: form.lot_size_sqft ? parseInt(form.lot_size_sqft) : undefined,
        construction_status: form.construction_status || undefined,
        year_built: form.year_built ? parseInt(form.year_built) : undefined,
        ...(isRent ? {
          association_fee: form.association && form.association_fee ? (assocFeeCurrency === 'DOP' ? Math.round(parseFloat(form.association_fee) / dopRate) : parseFloat(form.association_fee)) : undefined,
          deposit_policy: form.deposit_policy || undefined,
          included_utilities: form.included_utilities.length ? form.included_utilities : undefined,
        } : {
          roi: form.roi ? parseFloat(form.roi) : undefined,
          seller_financing: form.seller_financing, hoa: form.hoa,
          hoa_fee: form.hoa && form.hoa_fee ? (hoaFeeCurrency === 'DOP' ? Math.round(parseFloat(form.hoa_fee) / dopRate) : parseFloat(form.hoa_fee)) : undefined,
          tax_exempt: form.tax_exempt,
        }),
        gated_community: form.gated_community,
        features: form.features.length ? form.features : undefined,
        tags: form.tags.length ? form.tags : undefined,
        video_links: form.video_links.filter(v => v.trim()).length ? form.video_links.filter(v => v.trim()) : undefined,
        tour_3d_url: form.tour_3d_url.trim() || undefined,
        utilities: form.utilities.trim() || undefined,
        maps_url: form.maps_url.trim() || undefined,
        latitude: form.latitude ? parseFloat(form.latitude) : undefined,
        longitude: form.longitude ? parseFloat(form.longitude) : undefined,
        images: orderedImages.length ? orderedImages : undefined,
        co_listing_enabled: form.co_listing_enabled,
        co_listing_brokerage: form.co_listing_brokerage.trim() || undefined,
        co_listing_agent_name: form.co_listing_agent_name.trim() || undefined,
        co_listing_brokerage_email: form.co_listing_brokerage_email.trim() || undefined,
        co_listing_brokerage_phone: (() => { const p = (form.co_listing_brokerage_phone as string).trim(); return p && isValidPhoneNumber(p) ? p : undefined })(),
        co_listing_commission_split: form.co_listing_commission_split ? parseInt(form.co_listing_commission_split) : undefined,
        co_listing_notes: form.co_listing_notes.trim() || undefined,
        co_listing_status: form.co_listing_status || undefined,
        price_per_day: isRent && form.price_per_day ? (dayRateCurrency === 'DOP' ? Math.round(parseFloat(form.price_per_day) / dopRate) : parseFloat(form.price_per_day)) : undefined,
        price_per_month: isRent && form.price_per_month ? (monthRateCurrency === 'DOP' ? Math.round(parseFloat(form.price_per_month) / dopRate) : parseFloat(form.price_per_month)) : undefined,
        co_listing_agreement_accepted: form.co_listing_enabled ? form.co_listing_agreement_accepted as boolean : false,
        co_listing_agreement_url: form.co_listing_enabled && form.co_listing_agreement_url ? form.co_listing_agreement_url as string : undefined,
      })
      toast.success(t('submit_listing.toast_published'))
      setForm(EMPTY_FORM); setUploadedUrls([]); setThumbnail(null)
      go('listings')
    } catch { toast.error(t('submit_listing.toast_error')) }
    finally { setSubmitting(false) }
  }

  return (
    <div className="max-w-3xl mx-auto">
      <button type="button" onClick={() => go('listings')}
        className="flex items-center gap-1.5 text-dim text-[13px] mb-6 bg-transparent border-0 cursor-pointer hover:text-ink transition-colors">
        <ArrowLeft size={14} /> {t('submit_listing.back_to_listings')}
      </button>

      <form onSubmit={handleSubmit} className="space-y-4">
        <AdminFormBody
          form={form} set={set} tone={tone}
          uploadedUrls={uploadedUrls} thumbnail={thumbnail} uploading={uploading}
          fileInputRef={fileInputRef} handleFileChange={handleFileChange} removeImage={removeImage} setThumbnail={setThumbnail}
          priceCurrency={priceCurrency} setPriceCurrency={setPriceCurrency}
          hoaFeeCurrency={hoaFeeCurrency} setHoaFeeCurrency={setHoaFeeCurrency}
          dayRateCurrency={dayRateCurrency} setDayRateCurrency={setDayRateCurrency}
          monthRateCurrency={monthRateCurrency} setMonthRateCurrency={setMonthRateCurrency}
          assocFeeCurrency={assocFeeCurrency} setAssocFeeCurrency={setAssocFeeCurrency}
          dopRate={dopRate}
          customInput={customInput} setCustomInput={setCustomInput}
          customTagInput={customTagInput} setCustomTagInput={setCustomTagInput}
          customTags={customTags} addCustomFeature={addCustomFeature}
          addCustomTag={addCustomTag} removeCustomTag={removeCustomTag} toggleFeature={toggleFeature}
          customUtilityInput={customUtilityInput} setCustomUtilityInput={setCustomUtilityInput}
          customUtilities={customUtilities} addCustomUtility={addCustomUtility} removeCustomUtility={removeCustomUtility}
          agreementUploading={agreementUploading} onAgreementUpload={handleAgreementUpload}
        />
        <div className="flex gap-3 pt-2 pb-6">
          <button type="button" onClick={() => go('listings')} className="px-6 py-3 rounded-full border border-line bg-paper text-ink text-[13.5px] font-semibold cursor-pointer">
            {t('submit_listing.cancel')}
          </button>
          <button type="submit" disabled={submitting} className="px-8 py-3 rounded-full text-white text-[13.5px] font-bold border-0 cursor-pointer disabled:opacity-50 transition-opacity" style={{ background: tone }}>
            {submitting ? t('submit_listing.publishing') : t('submit_listing.publish')}
          </button>
        </div>
      </form>
    </div>
  )
}

// ── Edit existing listing ─────────────────────────────────────────────────────

export function AdminEditListing({ listing, onBack, onSaved }: {
  listing: AdminListing; onBack: () => void; onSaved: (updated: AdminListing) => void
}) {
  const { t } = useTranslation('admin')
  const [form, setFormState] = useState({
    title:               listing.title,
    type:                listing.type,
    transaction:         listing.transaction,
    location:            listing.location,
    price:               String(listing.price),
    description:         listing.description ?? '',
    description_es:      listing.description_es ?? '',
    bedrooms:            listing.bedrooms      != null ? String(listing.bedrooms)      : '',
    bathrooms:           listing.bathrooms     != null ? String(listing.bathrooms)     : '',
    area_sqft:           listing.area_sqft     != null ? String(listing.area_sqft)     : '',
    lot_size_sqft:       listing.lot_size_sqft != null ? String(listing.lot_size_sqft) : '',
    construction_status: listing.construction_status ?? '',
    year_built:          listing.year_built    != null ? String(listing.year_built)    : '',
    roi:                 listing.roi           != null ? String(listing.roi)           : '',
    seller_financing:    listing.seller_financing,
    hoa:                 listing.hoa,
    hoa_fee:             listing.hoa_fee       != null ? String(listing.hoa_fee)       : '',
    tax_exempt:          listing.tax_exempt,
    gated_community:     listing.gated_community,
    features:            listing.features ?? [],
    maps_url:            listing.maps_url ?? '',
    latitude:            listing.latitude  != null ? String(listing.latitude)  : '',
    longitude:           listing.longitude != null ? String(listing.longitude) : '',
    tags:                listing.tags ?? [],
    video_links:         listing.video_links ?? [],
    tour_3d_url:         listing.tour_3d_url ?? '',
    utilities:           listing.utilities ?? '',
    included_utilities:  listing.included_utilities ?? [],
    association:         listing.association_fee != null && listing.association_fee > 0,
    association_fee:     listing.association_fee != null ? String(listing.association_fee) : '',
    deposit_policy:      listing.deposit_policy ?? '',
    co_listing_enabled:          listing.co_listing_enabled ?? false,
    co_listing_brokerage:        listing.co_listing_brokerage ?? '',
    co_listing_agent_name:       listing.co_listing_agent_name ?? '',
    co_listing_brokerage_email:  listing.co_listing_brokerage_email ?? '',
    co_listing_brokerage_phone:  listing.co_listing_brokerage_phone ?? '',
    co_listing_commission_split: listing.co_listing_commission_split != null ? String(listing.co_listing_commission_split) : '',
    co_listing_notes:            listing.co_listing_notes ?? '',
    co_listing_status:           listing.co_listing_status ?? '',
    price_per_day:               listing.price_per_day != null ? String(listing.price_per_day) : '',
    price_per_month:             listing.price_per_month != null ? String(listing.price_per_month) : '',
    owner_paypal_email:          listing.owner_paypal_email ?? '',
    co_listing_agreement_accepted: listing.co_listing_agreement_accepted ?? false,
    co_listing_agreement_url:    listing.co_listing_agreement_url ?? '',
  })

  const [uploadedUrls, setUploadedUrls] = useState<string[]>(listing.images ?? [])
  const [thumbnail,    setThumbnail]    = useState<string | null>(listing.images?.[0] ?? null)
  const [uploading,    setUploading]    = useState(false)
  const [submitting,   setSubmitting]   = useState(false)
  const [priceCurrency, setPriceCurrency] = useState<'USD' | 'DOP'>('USD')
  const [hoaFeeCurrency, setHoaFeeCurrency] = useState<'USD' | 'DOP'>('USD')
  const [dayRateCurrency, setDayRateCurrency] = useState<'USD' | 'DOP'>('USD')
  const [monthRateCurrency, setMonthRateCurrency] = useState<'USD' | 'DOP'>('USD')
  const [assocFeeCurrency, setAssocFeeCurrency] = useState<'USD' | 'DOP'>('USD')
  const [dopRate, setDopRate] = useState(59.5)
  const [customInput, setCustomInput] = useState('')
  const [customTagInput, setCustomTagInput] = useState('')
  const [customTags, setCustomTags] = useState<string[]>(() =>
    (listing.tags ?? []).filter(tag => !BASE_TAGS.includes(tag))
  )
  const [customUtilityInput, setCustomUtilityInput] = useState('')
  const [customUtilities, setCustomUtilities] = useState<string[]>(() =>
    (listing.included_utilities ?? []).filter(u => !INCLUDED_UTILITIES.includes(u))
  )
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [agreementUploading, setAgreementUploading] = useState(false)

  useEffect(() => {
    fetch('https://open.er-api.com/v6/latest/USD')
      .then(r => r.json())
      .then(d => { if (d.rates?.DOP) setDopRate(d.rates.DOP) })
      .catch(() => {})
  }, [])

  function set(f: string, v: unknown) { setFormState(p => ({ ...p, [f]: v })) }

  function toggleFeature(f: string) {
    set('features', form.features.includes(f) ? form.features.filter(x => x !== f) : [...form.features, f])
  }

  function addCustomFeature() {
    const toTitle = (s: string) => s.trim().toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    const entries = customInput.split(',').map(toTitle).filter(s => s && !form.features.includes(s))
    if (entries.length) set('features', [...form.features, ...entries])
    setCustomInput('')
  }

  function addCustomTag() {
    const val = customTagInput.trim().toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    if (!val || customTags.includes(val) || BASE_TAGS.includes(val)) return
    setCustomTags(prev => [...prev, val])
    set('tags', [...form.tags, val])
    setCustomTagInput('')
  }

  function removeCustomTag(tag: string) {
    setCustomTags(prev => prev.filter(x => x !== tag))
  }

  function addCustomUtility() {
    const toTitle = (s: string) => s.trim().toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    const entries = customUtilityInput.split(',').map(toTitle).filter(s => s && !INCLUDED_UTILITIES.includes(s) && !customUtilities.includes(s))
    if (!entries.length) return
    setCustomUtilities(prev => [...prev, ...entries])
    set('included_utilities', [...(form.included_utilities as string[]), ...entries])
    setCustomUtilityInput('')
  }

  function removeCustomUtility(u: string) {
    setCustomUtilities(prev => prev.filter(x => x !== u))
    set('included_utilities', (form.included_utilities as string[]).filter((x: string) => x !== u))
  }

  async function handleAgreementUpload(file: File) {
    setAgreementUploading(true)
    try {
      const url = await uploadAgreementPdf(file)
      set('co_listing_agreement_url', url)
    } catch {
      toast.error(t('submit_listing.toast_upload_fail'))
    } finally {
      setAgreementUploading(false)
    }
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    if (!files.length) return
    if (uploadedUrls.length + files.length > 25) { toast.error(t('submit_listing.toast_max_photos')); return }
    setUploading(true)
    try {
      const newUrls = await uploadListingImages(files)
      setUploadedUrls(prev => { const merged = [...prev, ...newUrls]; if (!thumbnail) setThumbnail(merged[0]); return merged })
    } catch { toast.error(t('submit_listing.toast_upload_fail')) }
    finally { setUploading(false); if (fileInputRef.current) fileInputRef.current.value = '' }
  }

  function removeImage(url: string) {
    setUploadedUrls(prev => { const next = prev.filter(u => u !== url); if (thumbnail === url) setThumbnail(next[0] ?? null); return next })
  }

  async function handleSubmit(e: React.SyntheticEvent<HTMLFormElement>) {
    e.preventDefault()
    const isRentMode = form.transaction === 'rent' || form.transaction === 'both'
    if (!form.title.trim() || !form.location || (!isRentMode && !form.price)) { toast.error(t('submit_listing.toast_required')); return }
    if (isRentMode && !(form.price_per_day as string) && !(form.price_per_month as string)) { toast.error(t('submit_listing.toast_rent_price_required')); return }
    if (PAYPAL_ENABLED && isRentMode && (form.price_per_day as string) && !(form.owner_paypal_email as string).trim()) { toast.error('Owner PayPal email is required when a daily rate is set.'); return }
    if (form.co_listing_enabled && !form.co_listing_agreement_accepted) { toast.error('You must accept the co-listing terms before saving.'); return }
    setSubmitting(true)
    try {
      const orderedImages = thumbnail ? [thumbnail, ...uploadedUrls.filter(u => u !== thumbnail)] : uploadedUrls
      const isRent = isRentMode
      const priceUSD = isRent
        ? ((form.price_per_day as string) ? (dayRateCurrency === 'DOP' ? Math.round(parseFloat(form.price_per_day as string) / dopRate) : parseFloat(form.price_per_day as string)) : (monthRateCurrency === 'DOP' ? Math.round(parseFloat(form.price_per_month as string) / dopRate) : parseFloat(form.price_per_month as string)))
        : (priceCurrency === 'DOP' ? Math.round(parseFloat(form.price) / dopRate) : parseFloat(form.price))
      const updated = await updateListing(listing.id, {
        title: form.title.trim(), description: form.description || undefined,
        description_es: form.description_es || undefined,
        type: form.type, transaction: form.transaction,
        price: priceUSD, location: form.location,
        bedrooms: form.bedrooms ? parseInt(form.bedrooms) : undefined,
        bathrooms: form.bathrooms ? parseFloat(form.bathrooms) : undefined,
        area_sqft: form.area_sqft ? parseInt(form.area_sqft) : undefined,
        lot_size_sqft: form.lot_size_sqft ? parseInt(form.lot_size_sqft) : undefined,
        construction_status: form.construction_status || undefined,
        year_built: form.year_built ? parseInt(form.year_built) : undefined,
        ...(isRent ? {
          association_fee: form.association && form.association_fee ? (assocFeeCurrency === 'DOP' ? Math.round(parseFloat(form.association_fee) / dopRate) : parseFloat(form.association_fee)) : undefined,
          deposit_policy: form.deposit_policy || undefined,
          included_utilities: form.included_utilities,
        } : {
          roi: form.roi ? parseFloat(form.roi) : undefined,
          seller_financing: form.seller_financing, hoa: form.hoa,
          hoa_fee: form.hoa && form.hoa_fee ? (hoaFeeCurrency === 'DOP' ? Math.round(parseFloat(form.hoa_fee) / dopRate) : parseFloat(form.hoa_fee)) : undefined,
          tax_exempt: form.tax_exempt,
        }),
        gated_community: form.gated_community,
        features: form.features,
        tags: form.tags,
        video_links: form.video_links.filter(v => v.trim()),
        tour_3d_url: form.tour_3d_url.trim() || undefined,
        utilities: form.utilities.trim() || undefined,
        maps_url: form.maps_url.trim() || undefined,
        latitude: form.latitude ? parseFloat(form.latitude) : undefined,
        longitude: form.longitude ? parseFloat(form.longitude) : undefined,
        images: orderedImages,
        co_listing_enabled: form.co_listing_enabled as boolean,
        co_listing_brokerage: (form.co_listing_brokerage as string).trim() || undefined,
        co_listing_agent_name: (form.co_listing_agent_name as string).trim() || undefined,
        co_listing_brokerage_email: (form.co_listing_brokerage_email as string).trim() || undefined,
        co_listing_brokerage_phone: (() => { const p = (form.co_listing_brokerage_phone as string).trim(); return p && isValidPhoneNumber(p) ? p : undefined })(),
        co_listing_commission_split: (form.co_listing_commission_split as string) ? parseInt(form.co_listing_commission_split as string) : undefined,
        co_listing_notes: (form.co_listing_notes as string).trim() || undefined,
        co_listing_status: (form.co_listing_status as string) || undefined,
        price_per_day: isRent && (form.price_per_day as string) ? (dayRateCurrency === 'DOP' ? Math.round(parseFloat(form.price_per_day as string) / dopRate) : parseFloat(form.price_per_day as string)) : undefined,
        price_per_month: isRent && (form.price_per_month as string) ? (monthRateCurrency === 'DOP' ? Math.round(parseFloat(form.price_per_month as string) / dopRate) : parseFloat(form.price_per_month as string)) : undefined,
        owner_paypal_email: isRent && (form.price_per_day as string) && (form.owner_paypal_email as string) ? (form.owner_paypal_email as string) : undefined,
        co_listing_agreement_accepted: (form.co_listing_enabled as boolean) ? (form.co_listing_agreement_accepted as boolean) : false,
        co_listing_agreement_url: (form.co_listing_enabled as boolean) && (form.co_listing_agreement_url as string) ? (form.co_listing_agreement_url as string) : undefined,
      })
      toast.success(t('submit_listing.toast_saved'))
      onSaved({
        ...listing, ...updated,
        submitted_by: listing.submitted_by,
        submitted_by_name: listing.submitted_by_name,
        submitted_by_email: listing.submitted_by_email,
        reviewed_by_name: listing.reviewed_by_name,
        reviewed_by_email: listing.reviewed_by_email,
        reviewed_at: listing.reviewed_at,
      })
    } catch { toast.error(t('submit_listing.toast_error')) }
    finally { setSubmitting(false) }
  }

  return (
    <div className="max-w-3xl mx-auto">
      <button type="button" onClick={onBack}
        className="flex items-center gap-1.5 text-dim text-[13px] mb-4 bg-transparent border-0 cursor-pointer hover:text-ink transition-colors">
        <ArrowLeft size={14} /> {t('submit_listing.back_to_listing')}
      </button>
      <h2 className="text-[20px] font-bold text-ink mb-5">{t('submit_listing.edit_heading')}</h2>

      {listing.status === 'active' && <RoomPhotoTourEditor key={listing.id} listingId={listing.id} />}

      <form onSubmit={handleSubmit} className="space-y-4">
        <AdminFormBody
          form={form} set={set} tone={TONE}
          uploadedUrls={uploadedUrls} thumbnail={thumbnail} uploading={uploading}
          fileInputRef={fileInputRef} handleFileChange={handleFileChange} removeImage={removeImage} setThumbnail={setThumbnail}
          priceCurrency={priceCurrency} setPriceCurrency={setPriceCurrency}
          hoaFeeCurrency={hoaFeeCurrency} setHoaFeeCurrency={setHoaFeeCurrency}
          dayRateCurrency={dayRateCurrency} setDayRateCurrency={setDayRateCurrency}
          monthRateCurrency={monthRateCurrency} setMonthRateCurrency={setMonthRateCurrency}
          assocFeeCurrency={assocFeeCurrency} setAssocFeeCurrency={setAssocFeeCurrency}
          dopRate={dopRate}
          customInput={customInput} setCustomInput={setCustomInput}
          customTagInput={customTagInput} setCustomTagInput={setCustomTagInput}
          customTags={customTags} addCustomFeature={addCustomFeature}
          addCustomTag={addCustomTag} removeCustomTag={removeCustomTag} toggleFeature={toggleFeature}
          customUtilityInput={customUtilityInput} setCustomUtilityInput={setCustomUtilityInput}
          customUtilities={customUtilities} addCustomUtility={addCustomUtility} removeCustomUtility={removeCustomUtility}
          agreementUploading={agreementUploading} onAgreementUpload={handleAgreementUpload}
        />
        <div className="flex gap-3 pt-2 pb-6">
          <button type="button" onClick={onBack} className="px-6 py-3 rounded-full border border-line bg-paper text-ink text-[13.5px] font-semibold cursor-pointer">
            {t('submit_listing.cancel')}
          </button>
          <button type="submit" disabled={submitting} className="px-8 py-3 rounded-full text-white text-[13.5px] font-bold border-0 cursor-pointer disabled:opacity-50 transition-opacity" style={{ background: TONE }}>
            {submitting ? t('submit_listing.saving') : t('submit_listing.save')}
          </button>
        </div>
      </form>
    </div>
  )
}
