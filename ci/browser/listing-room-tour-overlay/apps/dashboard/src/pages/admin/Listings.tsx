import { useEffect, useState, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Check, X, Home, Search, MoreHorizontal, Archive,
  MapPin, Pencil, GitCompare, Eye, Plus, CheckCircle2, XCircle, Star, Clock, Sparkles, UserCheck, UserX,
} from 'lucide-react'
import toast from 'react-hot-toast'
import {
  getAdminListings, getAdminListing, approveAdminListing, rejectAdminListing, archiveAdminListing,
  getAdminListingEdits, approveListingEdit, approveListingRoomPhotoTour, rejectListingEdit,
  getAdminDealRequests, approveDealRequest, rejectDealRequest,
  clearListingDeal, setListingDeal, assignAdminListing,
  getAdminActivityLog, getAdminAgents, getAdminStats,
} from '../../api/admin'
import type { AdminListing, AdminListingEdit, ActivityEntry, DealRequest, AdminAgent, AdminStats } from '../../api/admin'
import { AdminEditListing, AdminSubmitListing } from './SubmitListing'
import { TONE, FilterPills } from './shared'
import { ListingDetailPanel } from '../../components/admin/ListingDetailPanel'
import { ListingEditReviewPage } from '../../components/admin/ListingEditReviewPage'
import { RoomPhotoTourEditReview } from '../../components/admin/RoomPhotoTourEditReview'
import { ListingHistoryPanel } from '../../components/admin/ListingHistoryPanel'
import { ConfirmModal } from '../../components/shared/ConfirmModal'

const titleCase = (s: string) =>
  s === s.toUpperCase() ? s.toLowerCase().replace(/\b\w/g, c => c.toUpperCase()) : s

function pageWindow(current: number, total: number): (number | '…')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const pages = new Set([1, total, current - 1, current, current + 1])
  const sorted = [...pages].filter(p => p >= 1 && p <= total).sort((a, b) => a - b)
  const out: (number | '…')[] = []
  let prev = 0
  for (const p of sorted) {
    if (prev && p - prev > 1) out.push('…')
    out.push(p)
    prev = p
  }
  return out
}

function PaginationBar({
  page, totalPages, total, pageSize, tone, border, onPrev, onNext, onSetPage,
}: {
  page: number; totalPages: number; total: number; pageSize: number; tone: string
  border: 'border-b' | 'border-t'
  onPrev: () => void; onNext: () => void; onSetPage: (p: number) => void
}) {
  const { t } = useTranslation('admin')
  return (
    <div className={`flex items-center justify-between gap-3 flex-wrap px-4 sm:px-5.5 py-3.5 ${border} border-line`}>
      <span className="text-[12px] text-dim">
        {t('listings_page.showing_range', {
          from: total === 0 ? 0 : (page - 1) * pageSize + 1,
          to: Math.min(page * pageSize, total),
          total,
        })}
      </span>
      {totalPages > 1 && (
        <div className="flex items-center gap-1.5">
          <button
            disabled={page <= 1}
            onClick={onPrev}
            className="py-1.5 px-3 rounded-lg border border-line bg-paper text-ink text-[12px] font-semibold cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {t('listings_page.prev')}
          </button>
          {pageWindow(page, totalPages).map((p, i) =>
            p === '…' ? (
              <span key={`e${i}`} className="px-1 text-dim text-[12px]">…</span>
            ) : (
              <button
                key={p}
                onClick={() => onSetPage(p)}
                className="w-8 h-8 rounded-lg text-[12px] font-semibold cursor-pointer"
                style={{
                  background: p === page ? tone : 'transparent',
                  color: p === page ? '#fff' : '#33425f',
                  border: `1px solid ${p === page ? tone : '#e4ddcf'}`,
                }}
              >
                {p}
              </button>
            )
          )}
          <button
            disabled={page >= totalPages}
            onClick={onNext}
            className="py-1.5 px-3 rounded-lg border border-line bg-paper text-ink text-[12px] font-semibold cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {t('listings_page.next')}
          </button>
        </div>
      )}
    </div>
  )
}

const STATUS_STYLE: Record<string, { color: string; bg: string; label: string; accent: string }> = {
  pending_approval: { color: '#a16207', bg: '#fef9c3', label: 'Pending',  accent: '#f0a800' },
  active:           { color: '#15803d', bg: '#dcfce7', label: 'Active',   accent: '#1f7a3d' },
  rejected:         { color: '#b91c1c', bg: '#fee2e2', label: 'Rejected', accent: '#e10f1f' },
  archived:         { color: '#6b7280', bg: '#f3f4f6', label: 'Archived', accent: '#9ca3af' },
}

function fmtPrice(n: number) {
  if (n >= 1_000_000_000_000) return `$${(n / 1_000_000_000_000).toFixed(2)}T`
  if (n >= 1_000_000_000)     return `$${(n / 1_000_000_000).toFixed(2)}B`
  return n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(2)}M` : `$${Math.round(n / 1_000)}K`
}

function fmtListingPrice(l: AdminListing) {
  if (l.transaction === 'rent') {
    const parts = []
    if (l.price_per_day)   parts.push(`$${Number(l.price_per_day).toLocaleString()}/day`)
    if (l.price_per_month) parts.push(`$${Number(l.price_per_month).toLocaleString()}/mo`)
    if (parts.length) return parts.join(' · ')
  }
  return fmtPrice(l.price)
}

function fmtType(t: string) {
  return t.charAt(0).toUpperCase() + t.slice(1).toLowerCase()
}

function fmtRelative(dateStr: string | null | undefined, t: (key: string, opts?: Record<string, unknown>) => string): string {
  if (!dateStr) return '—'
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1)    return t('rel.just_now')
  if (mins < 60)   return t('rel.mins_ago', { count: mins })
  const hrs = Math.floor(mins / 60)
  if (hrs < 24)    return t('rel.hrs_ago', { count: hrs })
  const days = Math.floor(hrs / 24)
  if (days < 30)   return t('rel.days_ago', { count: days })
  const months = Math.floor(days / 30)
  if (months < 12) return t('rel.months_ago', { count: months })
  return t('rel.years_ago', { count: Math.floor(months / 12) })
}

function StatusChip({ status }: { status: string }) {
  const { t } = useTranslation('admin')
  const s = STATUS_STYLE[status] ?? { color: '#6b7280', bg: '#f3f4f6', label: status }
  return (
    <span
      className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[11.5px] font-bold"
      style={{ background: s.bg, color: s.color }}
    >
      {t(`listings_page.status_${status}`, { defaultValue: s.label })}
    </span>
  )
}

function ActionMenu({ onView, onEdit, onHistory, onSetDeal, onArchive }: { onView: () => void; onEdit: () => void; onHistory: () => void; onSetDeal?: () => void; onArchive: () => void }) {
  const { t } = useTranslation('admin')
  const [open, setOpen] = useState(false)
  const [pos, setPos]   = useState({ top: 0, left: 0 })
  const btnRef  = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (
        menuRef.current && !menuRef.current.contains(e.target as Node) &&
        btnRef.current  && !btnRef.current.contains(e.target as Node)
      ) setOpen(false)
    }
    if (open) document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  function toggle() {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect()
      const menuW = 176
      const menuH = 200 // approximate; enough to flip up if near bottom
      const left = Math.max(8, Math.min(r.right - menuW, window.innerWidth - menuW - 8))
      const fitsBelow = r.bottom + 6 + menuH < window.innerHeight
      const top = fitsBelow ? r.bottom + 6 : r.top - menuH - 6
      setPos({ top, left })
    }
    setOpen(v => !v)
  }

  return (
    <>
      <button
        ref={btnRef}
        onClick={toggle}
        className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-line-soft cursor-pointer border-0 bg-transparent"
      >
        <MoreHorizontal size={15} className="text-dim" />
      </button>
      {open && (
        <div
          ref={menuRef}
          className="fixed w-44 bg-white border border-line rounded-xl shadow-lg z-50 py-1 overflow-hidden"
          style={{ top: pos.top, left: pos.left }}
        >
          <button
            onClick={() => { onView(); setOpen(false) }}
            className="w-full text-left flex items-center gap-2 px-3.5 py-2 text-[12.5px] text-ink hover:bg-line-soft cursor-pointer"
          >
            <Eye size={12} /> {t('listings_page.action_view')}
          </button>
          <button
            onClick={() => { onEdit(); setOpen(false) }}
            className="w-full text-left flex items-center gap-2 px-3.5 py-2 text-[12.5px] text-ink hover:bg-line-soft cursor-pointer"
          >
            <Pencil size={12} /> {t('listings_page.action_edit')}
          </button>
          <button
            onClick={() => { onHistory(); setOpen(false) }}
            className="w-full text-left flex items-center gap-2 px-3.5 py-2 text-[12.5px] text-ink hover:bg-line-soft cursor-pointer"
          >
            <Clock size={12} /> {t('listings_page.action_history')}
          </button>
          {onSetDeal && (
            <button
              onClick={() => { onSetDeal(); setOpen(false) }}
              className="w-full text-left flex items-center gap-2 px-3.5 py-2 text-[12.5px] text-amber-600 hover:bg-amber-50 cursor-pointer"
            >
              <Sparkles size={12} /> {t('listings_page.action_set_deal')}
            </button>
          )}
          <div className="border-t border-line mx-2" />
          <button
            onClick={() => { onArchive(); setOpen(false) }}
            className="w-full text-left flex items-center gap-2 px-3.5 py-2 text-[12.5px] text-red-500 hover:bg-red-50 cursor-pointer"
          >
            <Archive size={12} /> {t('listings_page.action_archive')}
          </button>
        </div>
      )}
    </>
  )
}

// ── Activity sidebar helpers ────────────────────────────────────────────────

type EventMeta = { Icon: typeof CheckCircle2; color: string }
const EVENT_META: Record<string, EventMeta> = {
  listing_approved:   { Icon: CheckCircle2, color: '#16a34a' },
  listing_rejected:   { Icon: XCircle,      color: '#dc2626' },
  listing_archived:   { Icon: Archive,      color: '#7884a0' },
  edit_approved:      { Icon: CheckCircle2, color: '#16a34a' },
  edit_rejected:      { Icon: XCircle,      color: '#dc2626' },
  listing_assigned:   { Icon: UserCheck,    color: '#0d9488' },
  listing_unassigned: { Icon: UserX,        color: '#7884a0' },
}
const DEFAULT_META: EventMeta = { Icon: CheckCircle2, color: '#7884a0' }
const LISTING_EVENTS = new Set(['listing_approved', 'listing_rejected', 'listing_archived', 'edit_approved', 'edit_rejected', 'listing_assigned', 'listing_unassigned'])

// ── Main component ─────────────────────────────────────────────────────────────

const STATUS_FILTERS = ['All', 'Active', 'Rejected', 'Archived'] as const
const CO_LISTING_FILTERS = ['All', 'Co-Listed', 'No Co-Listing'] as const
const COLS = 'grid-cols-[2fr_0.9fr_1fr_1fr_1.6fr_1fr_40px]'
const PAGE_SIZE = 50

function isRoomTourEdit(edit: AdminListingEdit) {
  return Object.prototype.hasOwnProperty.call(edit.proposed_data, 'room_photo_tour')
}

export function AdminListings() {
  const { t } = useTranslation('admin')
  const { t: tRoom } = useTranslation('common', { keyPrefix: 'room_photo_tour' })
  const [mainItems,    setMainItems]    = useState<AdminListing[]>([])
  const [mainTotal,    setMainTotal]    = useState(0)
  const [pendingItems, setPendingItems] = useState<AdminListing[]>([])
  const [dealItems,    setDealItems]    = useState<AdminListing[]>([])
  const [stats,        setStats]        = useState<AdminStats | null>(null)
  const [page,         setPage]         = useState(1)
  const [edits,        setEdits]        = useState<AdminListingEdit[]>([])
  const [dealRequests, setDealRequests] = useState<DealRequest[]>([])
  const [realtors,     setRealtors]     = useState<AdminAgent[]>([])
  const [filter,       setFilter]       = useState<string>('All')
  const [coFilter,     setCoFilter]     = useState<string>('All')
  const [query,        setQuery]        = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [loading,      setLoading]      = useState(true)
  const [tableLoading, setTableLoading] = useState(true)
  const [working,      setWorking]      = useState(false)
  const [rejectingDealId, setRejectingDealId] = useState<string | null>(null)
  const [dealRejectReason, setDealRejectReason] = useState('')
  const [setDealTarget,    setSetDealTarget]    = useState<AdminListing | null>(null)
  const [setDealValue,     setSetDealValue]     = useState('')
  const [setDealType,      setSetDealType]      = useState<'pct' | 'fixed'>('pct')
  const [selected,           setSelected]           = useState<AdminListing | null>(null)
  const [selectedEdit,       setSelectedEdit]       = useState<AdminListingEdit | null>(null)
  const [historyListing,     setHistoryListing]     = useState<AdminListing | null>(null)
  const [confirmArchive,     setConfirmArchive]     = useState<AdminListing | null>(null)
  const [confirmClearDealId, setConfirmClearDealId] = useState<string | null>(null)
  const [editing,         setEditing]         = useState(false)
  const [showAdd,      setShowAdd]      = useState(false)
  const [activity,     setActivity]     = useState<ActivityEntry[]>([])
  const [actLoading,   setActLoading]   = useState(true)
  const [searchParams, setSearchParams] = useSearchParams()

  async function loadMainTable() {
    setTableLoading(true)
    const res = await getAdminListings({
      page, pageSize: PAGE_SIZE,
      status: filter === 'All' ? undefined : filter.toLowerCase(),
      excludeStatus: filter === 'All' ? 'pending_approval' : undefined,
      coListingEnabled: coFilter === 'All' ? undefined : coFilter === 'Co-Listed',
      q: debouncedQuery.trim() || undefined,
    })
    setMainItems(res.items)
    setMainTotal(res.total)
    setTableLoading(false)
  }

  async function load() {
    setLoading(true)
    setActLoading(true)
    const [pendingRes, dealRes, editData, dealData, act, agentChoices, statsRes] = await Promise.all([
      getAdminListings({ status: 'pending_approval', pageSize: 200 }),
      getAdminListings({ isDeal: true, pageSize: 50 }),
      getAdminListingEdits(),
      getAdminDealRequests('pending'),
      getAdminActivityLog(25),
      getAdminAgents(),
      getAdminStats(),
    ])
    setPendingItems(pendingRes.items)
    setDealItems(dealRes.items)
    setEdits(editData)
    setDealRequests(dealData)
    setActivity(act)
    setRealtors(agentChoices)
    setStats(statsRes)
    setLoading(false)
    setActLoading(false)
    await loadMainTable()
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const didMountTable = useRef(false)
  useEffect(() => {
    if (!didMountTable.current) { didMountTable.current = true; return }
    loadMainTable()
  }, [page, filter, coFilter, debouncedQuery]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 350)
    return () => clearTimeout(timer)
  }, [query])

  // Any filter/search change resets to page 1; `page` is deliberately excluded
  // from these deps so this only fires on an actual filter change.
  useEffect(() => {
    setPage(1)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, coFilter, debouncedQuery])

  useEffect(() => {
    const openId = searchParams.get('openId')
    if (!openId) return
    getAdminListing(openId)
      .then(listing => { setSelected(listing); setSearchParams({}, { replace: true }) })
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function updateListingEverywhere(updated: AdminListing) {
    const apply = (l: AdminListing) => l.id === updated.id ? updated : l
    setMainItems(prev => prev.map(apply))
    setPendingItems(prev => prev.map(apply))
    setDealItems(prev => prev.map(apply))
  }

  const totalPages = Math.max(1, Math.ceil(mainTotal / PAGE_SIZE))

  const counts = {
    total:    (stats?.active_listings ?? 0) + (stats?.pending_listings ?? 0) + (stats?.rejected_listings ?? 0) + (stats?.archived_listings ?? 0),
    pending:  stats?.pending_listings ?? 0,
    active:   stats?.active_listings ?? 0,
    rejected: stats?.rejected_listings ?? 0,
    archived: stats?.archived_listings ?? 0,
    edits:    edits.length,
  }

  async function handleApprove(id: string) {
    setWorking(true)
    await approveAdminListing(id)
    setSelected(null)
    await load()
    setWorking(false)
    toast.success(t('listings_page.toast_approved'))
  }

  async function handleReject(id: string, reason: string) {
    setWorking(true)
    await rejectAdminListing(id, reason)
    setSelected(null)
    await load()
    setWorking(false)
    toast.success(t('listings_page.toast_rejected'))
  }

  async function handleApproveEdit(edit: AdminListingEdit, roomTourReviewed = false) {
    if (roomTourReviewed !== isRoomTourEdit(edit)) throw new Error('Invalid room-tour review state')
    setWorking(true)
    try {
      if (roomTourReviewed) await approveListingRoomPhotoTour(edit.id)
      else await approveListingEdit(edit.id)
      setSelectedEdit(null)
      await load()
      toast.success(t('listings_page.toast_edit_approved'))
    } finally { setWorking(false) }
  }

  async function handleRejectEdit(id: string, reason: string) {
    setWorking(true)
    try {
      await rejectListingEdit(id, reason)
      setSelectedEdit(null)
      await load()
      toast.success(t('listings_page.toast_edit_rejected'))
    } finally { setWorking(false) }
  }

  async function handleArchive(id: string) {
    setWorking(true)
    await archiveAdminListing(id)
    setSelected(null)
    await load()
    setWorking(false)
    toast.success(t('listings_page.toast_archived'))
  }

  async function handleApproveDeal(id: string) {
    setWorking(true)
    try {
      await approveDealRequest(id)
      await load()
      toast.success(t('listings_page.toast_deal_set'))
    } finally { setWorking(false) }
  }

  async function handleRejectDeal(id: string, reason: string) {
    setWorking(true)
    try {
      await rejectDealRequest(id, reason)
      setRejectingDealId(null)
      setDealRejectReason('')
      await load()
      toast.success(t('listings_page.toast_deal_request_rejected'))
    } finally { setWorking(false) }
  }

  async function handleSetDeal() {
    if (!setDealTarget) return
    setWorking(true)
    try {
      const val = setDealValue.trim() ? parseFloat(setDealValue) : null
      await setListingDeal(setDealTarget.id, val, setDealType)
      setSetDealTarget(null)
      setSetDealValue('')
      setSetDealType('pct')
      await load()
      toast.success(t('listings_page.toast_deal_set'))
    } finally { setWorking(false) }
  }

  function handleAssigned(listingId: string, realtorId: string | null, realtorName: string, realtorEmail: string) {
    const patch = {
      assigned_realtor_id: realtorId,
      assigned_realtor_name: realtorId ? realtorName : null,
      assigned_realtor_email: realtorId ? realtorEmail : null,
    }
    const apply = (l: AdminListing) => l.id === listingId ? { ...l, ...patch } : l
    setMainItems(prev => prev.map(apply))
    setPendingItems(prev => prev.map(apply))
    setDealItems(prev => prev.map(apply))
    setSelected(prev => prev && prev.id === listingId ? { ...prev, ...patch } : prev)
    toast.success(realtorId ? t('listings_page.toast_assigned') : t('listings_page.toast_unassigned'))
  }

  async function handleClearDeal(id: string) {
    setWorking(true)
    try {
      await clearListingDeal(id)
      await load()
      toast.success(t('listings_page.toast_deal_cleared'))
    } finally { setWorking(false) }
  }

  const kpis = [
    {
      label: t('listings_page.kpi_total'),
      value: counts.total,
      sub: t('listings_page.kpi_total_sub', { active: counts.active, archived: counts.archived }),
      accent: undefined as string | undefined,
    },
    {
      label: t('listings_page.kpi_active'),
      value: counts.active,
      sub: t('listings_page.kpi_active_sub'),
      accent: undefined,
    },
    {
      label: t('listings_page.kpi_pending'),
      value: counts.pending,
      sub: t('listings_page.kpi_pending_sub'),
      accent: counts.pending > 0 ? '#d97706' : undefined,
    },
    {
      label: t('listings_page.kpi_edits'),
      value: counts.edits,
      sub: t('listings_page.kpi_edits_sub'),
      accent: counts.edits > 0 ? '#7c3aed' : undefined,
    },
  ]

  if (showAdd) {
    return (
      <AdminSubmitListing
        go={() => { setShowAdd(false); load() }}
        tone="#0d9488"
      />
    )
  }

  if (editing && selected) {
    return (
      <AdminEditListing
        listing={selected}
        onBack={() => setEditing(false)}
        onSaved={(updated: AdminListing) => {
          const merged = { ...selected, ...updated }
          updateListingEverywhere(merged)
          setSelected(merged)
          setEditing(false)
        }}
      />
    )
  }

  return (
    <div className="flex gap-5 items-start">

      {/* ── Left column ───────────────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col gap-4">

        {/* KPI cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {loading
            ? Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="bg-paper border border-line rounded-xl px-4 py-4 animate-pulse space-y-2 min-w-0 overflow-hidden">
                  <div className="h-2.5 bg-line-soft rounded w-2/3" />
                  <div className="h-7 bg-line-soft rounded w-1/2" />
                  <div className="h-2.5 bg-line-soft rounded w-3/4" />
                </div>
              ))
            : kpis.map(k => (
                <div key={k.label} className="bg-paper border border-line rounded-xl px-4 py-4 min-w-0 overflow-hidden">
                  <div className="text-[11px] font-bold uppercase tracking-[.07em] text-dim mb-2 truncate">{k.label}</div>
                  <div
                    className="text-[28px] font-bold leading-none"
                    style={{ color: k.accent ?? 'var(--ink, #1a1e2e)' }}
                  >
                    {k.value}
                  </div>
                  <div className="text-[11px] text-dim mt-1.5 truncate">{k.sub}</div>
                </div>
              ))
          }
        </div>

        {/* ── Main card ─────────────────────────────────────────────────── */}
        <div className="bg-paper border border-line rounded-2xl overflow-hidden">

          {/* Toolbar */}
          <div className="px-4 sm:px-5.5 py-4 border-b border-line space-y-3">
            <div className="font-sans text-[17px] font-bold text-ink">
              {t('listings_page.title')}
              {!loading && <span className="ml-2 text-[13px] font-normal text-dim">({mainTotal})</span>}
            </div>
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-2 w-full sm:w-auto">
                <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-line bg-white flex-1 sm:w-55">
                  <Search size={13} className="text-dim" />
                  <input
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    placeholder={t('listings_page.search_ph')}
                    className="text-xs border-0 outline-none bg-transparent text-ink placeholder:text-dim flex-1"
                  />
                </div>
                <button
                  onClick={() => setShowAdd(true)}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-[12.5px] font-semibold text-white shrink-0 cursor-pointer border-0"
                  style={{ background: TONE }}
                >
                  <Plus size={13} /> {t('listings_page.add_listing')}
                </button>
              </div>
              <div className="flex items-center gap-2">
                <FilterPills options={[...STATUS_FILTERS]} value={filter} onChange={setFilter} />
                <div className="w-px h-4 bg-line shrink-0" />
                <FilterPills options={[...CO_LISTING_FILTERS]} value={coFilter} onChange={setCoFilter} />
              </div>
            </div>
          </div>

          {/* ── Pending Approval section ─────────────────────────────── */}
          {!loading && pendingItems.length > 0 && (
            <div className="border-b border-line">
              <div className="px-4 sm:px-5.5 py-2.5 bg-amber-50 border-b border-amber-100 flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
                <span className="text-[11px] font-bold uppercase tracking-[.07em] text-amber-700">
                  {t('listings_page.pending_approval_banner', { count: pendingItems.length })}
                </span>
              </div>
              <div className="divide-y divide-line-soft">
                {pendingItems.map(l => (
                  <div key={l.id} className="px-4 sm:px-5.5 py-3 flex items-center gap-3 hover:bg-amber-50/70 transition-colors cursor-pointer" onClick={() => setSelected(l)}>
                    {l.images?.[0] ? (
                      <img src={l.images[0]} alt="" className="w-10 h-10 sm:w-14 sm:h-9 rounded-lg object-cover shrink-0" />
                    ) : (
                      <div className="w-10 h-10 sm:w-14 sm:h-9 rounded-lg flex items-center justify-center shrink-0" style={{ background: `${TONE}18` }}>
                        <Home size={14} style={{ color: TONE }} />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="text-[13px] font-semibold text-ink truncate">{titleCase(l.title)}</div>
                      <div className="text-[11px] text-dim flex items-center gap-1 truncate"><MapPin size={9} />{l.location}</div>
                    </div>
                    <div className="text-[12px] text-dim shrink-0 hidden sm:block">
                      {l.submitted_by_name ?? '—'} · {fmtRelative(l.updated_at, t)}
                    </div>
                    {/* Desktop: text buttons */}
                    <div className="hidden sm:flex items-center gap-2 shrink-0" onClick={e => e.stopPropagation()}>
                      <button onClick={() => handleApprove(l.id)} disabled={working} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-semibold text-white cursor-pointer disabled:opacity-50 border-0" style={{ background: '#1f7a3d' }}>
                        <Check size={11} /> {t('listings_page.approve')}
                      </button>
                      <button onClick={() => setSelected(l)} disabled={working} className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-line text-[12px] font-semibold text-ink bg-paper cursor-pointer disabled:opacity-50">
                        <X size={11} /> {t('listings_page.reject')}
                      </button>
                    </div>
                    {/* Mobile: icon-only buttons */}
                    <div className="sm:hidden flex items-center gap-2 shrink-0" onClick={e => e.stopPropagation()}>
                      <button onClick={() => handleApprove(l.id)} disabled={working} className="w-8 h-8 rounded-full flex items-center justify-center border-0 cursor-pointer disabled:opacity-50 shrink-0" style={{ background: '#1f7a3d' }}>
                        <Check size={14} color="white" />
                      </button>
                      <button onClick={() => setSelected(l)} disabled={working} className="w-8 h-8 rounded-full flex items-center justify-center border border-line bg-paper cursor-pointer disabled:opacity-50 shrink-0">
                        <X size={14} className="text-ink" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Pending Edits section ────────────────────────────────── */}
          {!loading && edits.length > 0 && (
            <div className="border-b border-line">
              <div className="px-4 sm:px-5.5 py-2.5 bg-violet-50 border-b border-violet-100 flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-violet-400 shrink-0" />
                <span className="text-[11px] font-bold uppercase tracking-[.07em] text-violet-700">
                  {t('listings_page.pending_edits_banner', { count: edits.length })}
                </span>
              </div>
              <div className="divide-y divide-line-soft">
                {edits.map(edit => (
                  <div key={edit.id} className="px-4 sm:px-5.5 py-3 flex items-center gap-3 hover:bg-violet-50/70 transition-colors cursor-pointer" onClick={() => setSelectedEdit(edit)}>
                    {edit.listing_thumbnail ? (
                      <img src={edit.listing_thumbnail} alt="" className="w-10 h-10 sm:w-14 sm:h-9 rounded-lg object-cover shrink-0" />
                    ) : (
                      <div className="w-10 h-10 sm:w-14 sm:h-9 rounded-lg flex items-center justify-center shrink-0" style={{ background: '#7c3aed18' }}>
                        <GitCompare size={14} style={{ color: '#7c3aed' }} />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="text-[13px] font-semibold text-ink truncate">{titleCase(edit.listing_title)}</div>
                      <div className="text-[11px] text-dim flex items-center gap-1 truncate"><MapPin size={9} />{edit.listing_location}</div>
                    </div>
                    <div className="text-[12px] text-dim shrink-0 hidden sm:block">
                      {edit.submitted_by_name ?? '—'} · {fmtRelative(edit.submitted_at, t)}
                    </div>
                    {isRoomTourEdit(edit) ? (
                      <button type="button" aria-label={tRoom('admin_review')} disabled={working}
                        onClick={event => { event.stopPropagation(); setSelectedEdit(edit) }}
                        className="flex min-h-11 shrink-0 items-center gap-1 rounded-lg border border-violet-200 bg-violet-50 px-3 text-xs font-semibold text-ink disabled:opacity-50">
                        <GitCompare size={14} aria-hidden="true" /> {t('listings_page.review')}
                      </button>
                    ) : <>
                    {/* Desktop: text buttons for ordinary edits */}
                    <div className="hidden sm:flex items-center gap-2 shrink-0" onClick={e => e.stopPropagation()}>
                      <button onClick={() => handleApproveEdit(edit)} disabled={working} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-semibold text-white cursor-pointer disabled:opacity-50 border-0" style={{ background: '#1f7a3d' }}>
                        <Check size={11} /> {t('listings_page.approve')}
                      </button>
                      <button onClick={() => setSelectedEdit(edit)} disabled={working} className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-line text-[12px] font-semibold text-ink bg-paper cursor-pointer disabled:opacity-50">
                        <GitCompare size={11} /> {t('listings_page.review')}
                      </button>
                    </div>
                    {/* Mobile: icon-only buttons for ordinary edits */}
                    <div className="sm:hidden flex items-center gap-2 shrink-0" onClick={e => e.stopPropagation()}>
                      <button onClick={() => handleApproveEdit(edit)} disabled={working} className="w-8 h-8 rounded-full flex items-center justify-center border-0 cursor-pointer disabled:opacity-50 shrink-0" style={{ background: '#1f7a3d' }}>
                        <Check size={14} color="white" />
                      </button>
                      <button onClick={() => setSelectedEdit(edit)} disabled={working} className="w-8 h-8 rounded-full flex items-center justify-center border border-violet-200 bg-violet-50 cursor-pointer disabled:opacity-50 shrink-0">
                        <GitCompare size={13} color="#7c3aed" />
                      </button>
                    </div>
                    </>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Active Deals section ─────────────────────────────────── */}
          {!loading && dealItems.length > 0 && (
            <div className="border-b border-line">
              <div className="px-4 sm:px-5.5 py-2.5 bg-amber-50 border-b border-amber-100 flex items-center gap-2">
                <Star size={11} fill="#f59e0b" style={{ color: '#f59e0b' }} />
                <span className="text-[11px] font-bold uppercase tracking-[.07em] text-amber-700">
                  {t('listings_page.active_deals_banner', { count: dealItems.length })}
                </span>
              </div>

              <div className="divide-y divide-line-soft">
                {dealItems.map(l => (
                  <div key={l.id} className="px-4 sm:px-5.5 py-3 flex items-center gap-3 hover:bg-amber-50/50 transition-colors cursor-pointer" onClick={() => setSelected(l)}>
                    {l.images?.[0] ? (
                      <img src={l.images[0]} alt="" className="w-10 h-10 sm:w-14 sm:h-9 rounded-lg object-cover shrink-0" />
                    ) : (
                      <div className="w-10 h-10 sm:w-14 sm:h-9 rounded-lg flex items-center justify-center shrink-0" style={{ background: '#f0a80018' }}>
                        <Star size={14} style={{ color: '#f0a800' }} />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="text-[13px] font-semibold text-ink truncate">{titleCase(l.title)}</div>
                      <div className="text-[11px] text-dim flex items-center gap-1 truncate">
                        <MapPin size={9} />{l.location}
                        {l.deal_discount_value && (
                          <span className="ml-1 font-semibold" style={{ color: '#c07800' }}>
                            · {l.deal_discount_type === 'fixed'
                              ? t('listings_page.discount_off_fixed', { value: Number(l.deal_discount_value).toLocaleString() })
                              : t('listings_page.discount_off_pct', { value: l.deal_discount_value })}
                          </span>
                        )}
                      </div>
                    </div>
                    {/* Desktop: text button */}
                    <button onClick={e => { e.stopPropagation(); setConfirmClearDealId(l.id) }} disabled={working} className="hidden sm:flex items-center gap-1 px-3 py-1.5 rounded-lg border border-line text-[12px] font-semibold text-red-500 bg-paper hover:bg-red-50 cursor-pointer disabled:opacity-50 shrink-0">
                      <X size={11} /> {t('listings_page.clear_deal')}
                    </button>
                    {/* Mobile: icon button */}
                    <button onClick={e => { e.stopPropagation(); setConfirmClearDealId(l.id) }} disabled={working} className="sm:hidden w-8 h-8 rounded-full flex items-center justify-center border border-red-200 bg-red-50 cursor-pointer disabled:opacity-50 shrink-0">
                      <X size={14} color="#ef4444" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Deal Requests section ────────────────────────────────── */}
          {!loading && dealRequests.length > 0 && (
            <div className="border-b border-line">
              <div className="px-4 sm:px-5.5 py-2.5 bg-amber-50 border-b border-amber-100 flex items-center gap-2">
                <Star size={11} fill="#f59e0b" style={{ color: '#f59e0b' }} />
                <span className="text-[11px] font-bold uppercase tracking-[.07em] text-amber-700">
                  {t('listings_page.deal_requests_banner', { count: dealRequests.length })}
                </span>
              </div>
              <div className="divide-y divide-line-soft">
                {dealRequests.map(req => {
                  const isRejectOpen = rejectingDealId === req.id
                  return (
                    <div key={req.id} className="px-4 sm:px-5.5 py-3 hover:bg-amber-50/50 transition-colors">
                      <div className="flex items-center gap-3">
                        {req.listing_thumbnail ? (
                          <img src={req.listing_thumbnail} alt="" className="w-10 h-10 sm:w-14 sm:h-9 rounded-lg object-cover shrink-0" />
                        ) : (
                          <div className="w-10 h-10 sm:w-14 sm:h-9 rounded-lg flex items-center justify-center shrink-0" style={{ background: '#f0a80018' }}>
                            <Star size={14} style={{ color: '#f0a800' }} />
                          </div>
                        )}
                        <div className="flex-1 min-w-0">
                          <div className="text-[13px] font-semibold text-ink truncate">{titleCase(req.listing_title)}</div>
                          <div className="text-[11px] text-dim flex items-center gap-1 truncate">
                            <MapPin size={9} />{req.listing_location}
                            <span className="mx-1">·</span>
                            <span className="font-semibold" style={{ color: '#c07800' }}>
                              {req.discount_type === 'fixed'
                                ? t('listings_page.discount_off_fixed', { value: Number(req.discount_value).toLocaleString() })
                                : t('listings_page.discount_off_pct', { value: req.discount_value })}
                            </span>
                          </div>
                        </div>
                        <div className="text-[11px] text-dim shrink-0 hidden sm:block">
                          {req.requested_by_name ?? req.requested_by_email} · {fmtRelative(req.created_at, t)}
                        </div>
                        {/* Desktop: text buttons */}
                        <div className="hidden sm:flex items-center gap-2 shrink-0" onClick={e => e.stopPropagation()}>
                          <button onClick={() => handleApproveDeal(req.id)} disabled={working} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-semibold text-white cursor-pointer disabled:opacity-50 border-0" style={{ background: '#f0a800' }}>
                            <Check size={11} /> {t('listings_page.approve')}
                          </button>
                          <button onClick={() => { setRejectingDealId(isRejectOpen ? null : req.id); setDealRejectReason('') }} disabled={working} className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-line text-[12px] font-semibold text-ink bg-paper cursor-pointer disabled:opacity-50">
                            <X size={11} /> {t('listings_page.reject')}
                          </button>
                        </div>
                        {/* Mobile: icon buttons */}
                        <div className="sm:hidden flex items-center gap-2 shrink-0" onClick={e => e.stopPropagation()}>
                          <button onClick={() => handleApproveDeal(req.id)} disabled={working} className="w-8 h-8 rounded-full flex items-center justify-center border-0 cursor-pointer disabled:opacity-50 shrink-0" style={{ background: '#f0a800' }}>
                            <Check size={14} color="white" />
                          </button>
                          <button onClick={() => { setRejectingDealId(isRejectOpen ? null : req.id); setDealRejectReason('') }} disabled={working} className="w-8 h-8 rounded-full flex items-center justify-center border border-line bg-paper cursor-pointer disabled:opacity-50 shrink-0">
                            <X size={14} className="text-ink" />
                          </button>
                        </div>
                      </div>
                      {isRejectOpen && (
                        <div className="mt-2.5 flex gap-2">
                          <input type="text" value={dealRejectReason} onChange={e => setDealRejectReason(e.target.value)} placeholder={t('listings_page.reason_ph')} className="flex-1 px-3 py-2 rounded-lg border border-line bg-white text-[12.5px] text-ink outline-none focus:border-amber-400" autoFocus />
                          <button onClick={() => handleRejectDeal(req.id, dealRejectReason)} disabled={!dealRejectReason.trim() || working} className="px-4 py-2 rounded-lg text-[12px] font-bold text-white cursor-pointer disabled:opacity-50 border-0" style={{ background: '#e10f1f' }}>
                            {t('listings_page.confirm')}
                          </button>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Pagination (top) */}
          {!loading && !tableLoading && mainTotal > 0 && (
            <PaginationBar
              page={page} totalPages={totalPages} total={mainTotal} pageSize={PAGE_SIZE} tone={TONE} border="border-b"
              onPrev={() => setPage(p => p - 1)} onNext={() => setPage(p => p + 1)} onSetPage={setPage}
            />
          )}

          {/* Desktop table header */}
          <div className={`hidden sm:grid ${COLS} px-5.5 py-2.5 border-b border-line bg-nav/5`}>
            {[t('listings_page.header_property'), t('listings_page.header_type'), t('listings_page.header_price'), t('listings_page.header_status'), t('listings_page.header_submitted_by'), t('listings_page.header_updated'), ''].map((h, i) => (
              <div key={i} className="text-[11px] font-bold uppercase tracking-[.07em] text-dim">{h}</div>
            ))}
          </div>

          {/* Rows */}
          {loading || tableLoading ? (
            <div className="divide-y divide-line-soft">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="px-5.5 py-4 animate-pulse flex items-center gap-3">
                  <div className="w-14 h-9 rounded-lg bg-line-soft shrink-0" />
                  <div className="flex-1 space-y-2">
                    <div className="h-3.5 bg-line-soft rounded w-1/3" />
                    <div className="h-3 bg-line-soft rounded w-1/2" />
                  </div>
                </div>
              ))}
            </div>
          ) : mainItems.length === 0 ? (
            <div className="py-12 text-center text-sm text-dim">
              {query.trim() ? t('listings_page.no_results', { query }) : t('listings_page.no_listings')}
            </div>
          ) : (
            <div className="divide-y divide-line-soft">
              {mainItems.map(l => (
                <div key={l.id}>
                  {/* Desktop row */}
                  <div
                    className={`hidden sm:grid ${COLS} items-center px-5.5 py-3.5 cursor-pointer hover:bg-line-soft/40 transition-colors`}
                    onClick={() => setSelected(l)}
                  >
                    {/* Property */}
                    <div className="flex items-center gap-3 min-w-0 pr-4">
                      {l.images?.[0] ? (
                        <img src={l.images[0]} alt={l.title} className="w-14 h-9 rounded-lg object-cover shrink-0" />
                      ) : (
                        <div className="w-14 h-9 rounded-lg flex items-center justify-center shrink-0" style={{ background: `${TONE}18` }}>
                          <Home size={16} style={{ color: TONE }} />
                        </div>
                      )}
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <div className="text-[13px] font-semibold text-ink truncate">{titleCase(l.title)}</div>
                          {l.co_listing_enabled && (
                            <span className="shrink-0 px-1.5 py-0.5 rounded text-[9.5px] font-bold tracking-wide" style={{ background: '#ccfbf1', color: '#0f766e' }}>CO-LIST</span>
                          )}
                        </div>
                        <div className="text-[11px] text-dim flex items-center gap-1 truncate"><MapPin size={9} />{l.location}</div>
                      </div>
                    </div>
                    {/* Type */}
                    <div className="text-[12px] text-ink2">{fmtType(l.type)}</div>
                    {/* Price */}
                    <div className="text-[13px] font-semibold text-ink">{fmtListingPrice(l)}</div>
                    {/* Status */}
                    <div><StatusChip status={l.status} /></div>
                    {/* Submitted by */}
                    <div className="min-w-0">
                      <div className="text-[13px] font-semibold text-ink truncate">{l.submitted_by_name ?? '—'}</div>
                      <div className="text-[11px] text-dim truncate">{l.submitted_by_email ?? ''}</div>
                    </div>
                    {/* Updated */}
                    <div className="text-[12px] text-ink2">{fmtRelative(l.updated_at, t)}</div>
                    {/* Actions */}
                    <div onClick={e => e.stopPropagation()}>
                      <ActionMenu
                        onView={() => setSelected(l)}
                        onEdit={() => { setSelected(l); setEditing(true) }}
                        onHistory={() => setHistoryListing(l)}
                        onSetDeal={l.status === 'active' && !l.is_deal ? () => { setSetDealTarget(l); setSetDealValue(''); setSetDealType('pct') } : undefined}
                        onArchive={() => setConfirmArchive(l)}
                      />
                    </div>
                  </div>

                  {/* Mobile card */}
                  <div
                    className="sm:hidden px-4 py-3.5 cursor-pointer"
                    onClick={() => setSelected(l)}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2.5 min-w-0">
                        {l.images?.[0] ? (
                          <img src={l.images[0]} alt="" className="w-10 h-10 rounded-lg object-cover shrink-0" />
                        ) : (
                          <div className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0" style={{ background: `${TONE}18` }}>
                            <Home size={14} style={{ color: TONE }} />
                          </div>
                        )}
                        <div className="min-w-0">
                          <div className="text-[13.5px] font-semibold text-ink truncate">{titleCase(l.title)}</div>
                          <div className="text-[11px] text-dim truncate flex items-center gap-1"><MapPin size={9} />{l.location}</div>
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-1.5 shrink-0">
                        <StatusChip status={l.status} />
                        <div className="text-[11px] text-dim">{fmtListingPrice(l)}</div>
                      </div>
                    </div>
                    <div className="text-[11px] text-dim mt-2">{t('listings_page.mobile_row_sub', { type: fmtType(l.type), time: fmtRelative(l.updated_at, t) })}</div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Pagination */}
          {!loading && !tableLoading && mainTotal > 0 && (
            <PaginationBar
              page={page} totalPages={totalPages} total={mainTotal} pageSize={PAGE_SIZE} tone={TONE} border="border-t"
              onPrev={() => setPage(p => p - 1)} onNext={() => setPage(p => p + 1)} onSetPage={setPage}
            />
          )}
        </div>
      </div>{/* end left column */}

      {/* ── Activity Sidebar ─────────────────────────────────────────────── */}
      <div className="w-72 shrink-0 hidden xl:block bg-paper border border-line rounded-2xl overflow-hidden sticky top-4">
        <div className="px-4 py-3.5 border-b border-line">
          <div className="font-semibold text-[14px] text-ink">{t('listings_page.recent_activity')}</div>
        </div>
        {actLoading ? (
          <div className="divide-y divide-line-soft">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="px-4 py-3 animate-pulse flex gap-2.5">
                <div className="w-6 h-6 rounded-full bg-line-soft shrink-0" />
                <div className="flex-1 space-y-1.5">
                  <div className="h-3 bg-line-soft rounded w-3/4" />
                  <div className="h-2.5 bg-line-soft rounded w-1/2" />
                </div>
              </div>
            ))}
          </div>
        ) : activity.filter(e => LISTING_EVENTS.has(e.event_type)).length === 0 ? (
          <div className="py-8 text-center text-[12px] text-dim">{t('listings_page.no_activity')}</div>
        ) : (
          <div className="divide-y divide-line-soft max-h-150 overflow-y-auto">
            {activity.filter(e => LISTING_EVENTS.has(e.event_type)).map(entry => {
              const meta = EVENT_META[entry.event_type] ?? DEFAULT_META
              const { Icon } = meta
              return (
                <div key={entry.id} className="px-4 py-3 flex gap-2.5">
                  <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 mt-0.5" style={{ background: `${meta.color}18` }}>
                    <Icon size={11} style={{ color: meta.color }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] text-ink leading-snug">{entry.description}</div>
                    <div className="text-[10.5px] text-dim mt-0.5">{fmtRelative(entry.created_at, t)}</div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Detail slide-over */}
      {selected && (
        <ListingDetailPanel
          listing={selected}
          realtors={realtors}
          onClose={() => setSelected(null)}
          onEdit={() => setEditing(true)}
          onApprove={() => handleApprove(selected.id)}
          onReject={(reason) => handleReject(selected.id, reason)}
          onArchive={() => setConfirmArchive(selected)}
          onAssigned={handleAssigned}
          onSetDeal={selected.status === 'active' && !selected.is_deal
            ? async (value, type) => {
                await setListingDeal(selected.id, value, type)
                await load()
                toast.success(t('listings_page.toast_deal_set'))
              }
            : undefined}
          onClearDeal={selected.is_deal ? () => setConfirmClearDealId(selected.id) : undefined}
          working={working}
        />
      )}

      {/* History slide-over */}
      {historyListing && (
        <ListingHistoryPanel
          listingId={historyListing.id}
          listingTitle={historyListing.title}
          onClose={() => setHistoryListing(null)}
        />
      )}

      {/* Edit review — full screen */}
      {selectedEdit && (isRoomTourEdit(selectedEdit) ? (
        <RoomPhotoTourEditReview key={selectedEdit.id}
          edit={selectedEdit}
          working={working}
          onApprove={() => handleApproveEdit(selectedEdit, true)}
          onReject={(reason) => handleRejectEdit(selectedEdit.id, reason)}
          onClose={() => setSelectedEdit(null)}
        />
      ) : (
        <ListingEditReviewPage
          edit={selectedEdit}
          working={working}
          onApprove={() => handleApproveEdit(selectedEdit)}
          onReject={(reason) => handleRejectEdit(selectedEdit.id, reason)}
          onClose={() => setSelectedEdit(null)}
        />
      ))}

      {/* Archive confirmation */}
      {confirmArchive && (
        <ConfirmModal
          title={t('listings_page.confirm_archive_title')}
          description={t('listings_page.confirm_archive_desc', { title: titleCase(confirmArchive.title) })}
          confirmLabel={t('listings_page.confirm_archive_btn')}
          variant="danger"
          loading={working}
          onConfirm={async () => {
            await handleArchive(confirmArchive.id)
            setConfirmArchive(null)
          }}
          onCancel={() => setConfirmArchive(null)}
        />
      )}

      {/* Clear Deal confirmation */}
      {confirmClearDealId && (
        <ConfirmModal
          title={t('listings_page.confirm_clear_deal_title')}
          description={t('listings_page.confirm_clear_deal_desc')}
          confirmLabel={t('listings_page.confirm_clear_deal_btn')}
          variant="warning"
          loading={working}
          onConfirm={async () => {
            await handleClearDeal(confirmClearDealId)
            setConfirmClearDealId(null)
          }}
          onCancel={() => setConfirmClearDealId(null)}
        />
      )}

      {/* Set as Deal modal */}
      {setDealTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,16,46,0.45)' }}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6">
            <div className="flex items-center gap-2 mb-1">
              <Sparkles size={16} style={{ color: '#f59e0b' }} />
              <div className="font-bold text-[15px] text-ink">{t('listings_page.set_deal_title')}</div>
            </div>
            <div className="text-[12.5px] text-dim mb-4 truncate">{titleCase(setDealTarget.title)}</div>

            <div className="space-y-3">
              <div>
                <div className="text-[11.5px] font-semibold text-dim uppercase tracking-[.06em] mb-1.5">{t('listings_page.discount_type')}</div>
                <div className="flex rounded-lg border border-line overflow-hidden text-[12px] font-semibold">
                  {(['pct', 'fixed'] as const).map(dtype => (
                    <button
                      key={dtype}
                      type="button"
                      onClick={() => setSetDealType(dtype)}
                      className="flex-1 py-2 cursor-pointer transition-colors"
                      style={{ background: setDealType === dtype ? '#f59e0b' : 'white', color: setDealType === dtype ? 'white' : '#6b7280' }}
                    >
                      {dtype === 'pct' ? t('listings_page.discount_pct') : t('listings_page.discount_fixed')}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <div className="text-[11.5px] font-semibold text-dim uppercase tracking-[.06em] mb-1.5">
                  {t('listings_page.discount_value')} <span className="normal-case font-normal">{t('listings_page.discount_optional')}</span>
                </div>
                <div className="flex items-center border border-line rounded-lg overflow-hidden">
                  <span className="px-3 text-[13px] text-dim bg-line-soft border-r border-line py-2">
                    {setDealType === 'pct' ? '%' : '$'}
                  </span>
                  <input
                    type="text"
                    inputMode={setDealType === 'pct' ? 'decimal' : 'numeric'}
                    value={setDealType === 'fixed' && setDealValue
                      ? Number(setDealValue.replace(/,/g, '')).toLocaleString('en-US')
                      : setDealValue}
                    onChange={e => {
                      const raw = setDealType === 'fixed'
                        ? e.target.value.replace(/[^0-9]/g, '')
                        : e.target.value.replace(/[^0-9.]/g, '')
                      setSetDealValue(raw)
                    }}
                    placeholder={t('listings_page.discount_value_ph')}
                    className="flex-1 px-3 py-2 text-[13px] text-ink outline-none bg-white"
                  />
                </div>
              </div>
            </div>

            <div className="flex gap-2 mt-5">
              <button
                onClick={handleSetDeal}
                disabled={working}
                className="flex-1 py-2.5 rounded-xl text-[13px] font-bold text-white cursor-pointer disabled:opacity-50 border-0"
                style={{ background: '#f59e0b' }}
              >
                <Star size={13} className="inline mr-1.5" fill="white" />
                {t('listings_page.set_deal_btn')}
              </button>
              <button
                onClick={() => setSetDealTarget(null)}
                disabled={working}
                className="px-4 py-2.5 rounded-xl text-[13px] font-semibold text-ink border border-line bg-paper cursor-pointer disabled:opacity-50"
              >
                {t('listings_page.cancel')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
