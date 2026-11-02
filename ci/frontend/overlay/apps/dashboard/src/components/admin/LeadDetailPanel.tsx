import { X, Mail, Phone, Calendar, Home, Copy, CheckCheck, Check, UserCheck, Zap, ExternalLink, KeyRound } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { assignLead, updateLeadStatus, type Lead } from '../../api/leads'
import type { AdminAgent } from '../../api/admin'
import { TONE } from '../../pages/admin/shared'
import { parsePhone } from '../../utils/phone'
import { ConfirmModal } from '../shared/ConfirmModal'
import { RealtorCombobox } from './RealtorCombobox'
import { LeadTrackingDetails } from './LeadTrackingOverview'
import { ShareContactButton } from '../shared/ShareContactDialog'

const LANDING_URL = import.meta.env.VITE_LANDING_URL ?? 'https://ilovedrrealty.com'

const TYPE_COLOR: Record<string, string> = {
  property_inquiry: '#1f7a3d',
  booking:          '#0d9488',
  buyer_interest:   '#e10f1f',
  seller_interest:  '#f0a800',
  rental_interest:  '#006BFF',
}

const STATUS_COLOR: Record<string, string> = {
  new:       '#64748b',
  assigned:  '#0d9488',
  contacted: '#1f7a3d',
  closed:    '#94a3b8',
}

const STATUSES = ['new', 'assigned', 'contacted', 'closed'] as const

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}
function fmtDateTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit',
  })
}

interface Props {
  lead: Lead | null
  realtors: AdminAgent[]
  onClose: () => void
  onAssigned: (leadId: string, realtorId: string, realtorName: string) => void
  onStatusUpdated: (leadId: string, status: string) => void
  allowedStatuses?: readonly string[]
  updateStatusFn?: (leadId: string, status: string) => Promise<void>
  calendlyUrl?: string
  onOpenDelivery?: (leadId: string) => void
}

export function LeadDetailPanel({ lead, realtors, onClose, onAssigned, onStatusUpdated, allowedStatuses, updateStatusFn, calendlyUrl, onOpenDelivery }: Props) {
  const { t } = useTranslation('admin')

  const TYPE_LABEL: Record<string, string> = {
    property_inquiry: t('lead_panel.type_inquiry'),
    booking:          t('lead_panel.type_booking'),
    buyer_interest:   t('lead_panel.type_buyer'),
    seller_interest:  t('lead_panel.type_seller'),
    rental_interest:  t('lead_panel.type_renter'),
  }

  const STATUS_LABEL: Record<string, string> = {
    new:       t('lead_panel.status_new'),
    assigned:  t('lead_panel.status_assigned'),
    contacted: t('lead_panel.status_contacted'),
    closed:    t('lead_panel.status_closed'),
  }

  const [copiedEmail,    setCopiedEmail]    = useState(false)
  const [copiedPhone,    setCopiedPhone]    = useState(false)
  const [assignSaving,   setAssignSaving]   = useState(false)
  const [statusSaving,   setStatusSaving]   = useState(false)
  const [skipConfirm,    setSkipConfirm]    = useState<{ status: string; skipped: string[] } | null>(null)
  const [confirmAssignId, setConfirmAssignId] = useState<string | null>(null)

  if (!lead) return null

  const color = TYPE_COLOR[lead.type] ?? '#64748b'

  function copyEmail() {
    if (!lead?.email) return
    navigator.clipboard.writeText(lead.email).then(() => {
      setCopiedEmail(true)
      setTimeout(() => setCopiedEmail(false), 2000)
    })
  }

  function copyPhone() {
    if (!lead!.phone) return
    navigator.clipboard.writeText(lead!.phone).then(() => {
      setCopiedPhone(true)
      setTimeout(() => setCopiedPhone(false), 2000)
    })
  }

  async function handleAssign(realtorId: string) {
    setAssignSaving(true)
    try {
      await assignLead(lead!.id, realtorId)
      const r = realtors.find(r => r.id === realtorId)
      onAssigned(lead!.id, realtorId, r?.display_name || r?.email || '')
    } catch {}
    setAssignSaving(false)
  }

  function requestAssign(realtorId: string) {
    const isOwnerAccess = lead!.type === 'seller_interest' && !lead!.property_id
    if (isOwnerAccess) {
      setConfirmAssignId(realtorId)
    } else {
      handleAssign(realtorId)
    }
  }

  async function applyStatus(status: string) {
    setStatusSaving(true)
    try {
      await (updateStatusFn ?? updateLeadStatus)(lead!.id, status)
      onStatusUpdated(lead!.id, status)
    } catch {}
    setStatusSaving(false)
  }

  function handleStatus(status: string) {
    if (status === lead!.status) return
    const currentIndex = STATUSES.indexOf(lead!.status as typeof STATUSES[number])
    const targetIndex  = STATUSES.indexOf(status as typeof STATUSES[number])
    const skipped = STATUSES.slice(currentIndex + 1, targetIndex).map(s => STATUS_LABEL[s])
    if (skipped.length > 0) {
      setSkipConfirm({ status, skipped })
      return
    }
    applyStatus(status)
  }

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-y-0 right-0 w-full sm:w-100 z-50 flex flex-col overflow-hidden shadow-2xl">

        <div className="flex-1 overflow-y-auto bg-nav flex flex-col">

          {/* ── Navy header ── */}
          <div className="relative px-5 pt-12 pb-10 flex flex-col items-center gap-3 text-center">
            <button
              onClick={onClose}
              className="absolute top-4 right-4 w-8 h-8 flex items-center justify-center rounded-full cursor-pointer border-0"
              style={{ background: 'rgba(255,255,255,.12)' }}
            >
              <X size={15} color="rgba(255,255,255,.8)" />
            </button>

            {lead.from_user_avatar_url ? (
              <img
                src={lead.from_user_avatar_url}
                alt={lead.name}
                referrerPolicy="no-referrer"
                className="w-16 h-16 rounded-full object-cover shrink-0 ring-4 ring-white/20"
              />
            ) : (
              <div
                className="w-16 h-16 rounded-full grid place-items-center text-white text-2xl font-bold shrink-0"
                style={{ background: color }}
              >
                {lead.name[0]?.toUpperCase() ?? '?'}
              </div>
            )}

            <div className="text-[18px] font-bold text-white leading-tight text-center">{lead.name}</div>
            {lead.from_user_code && (
              <div className="text-[11px] font-mono font-semibold text-white/50 tracking-widest">
                #{lead.from_user_code.padStart(7, '0')}
              </div>
            )}
            <div className="text-[12px] text-white/50">{fmtDateTime(lead.created_at)}</div>

            <div className="flex items-center gap-2 flex-wrap justify-center">
              <span
                className="text-[11.5px] font-bold px-3 py-0.75 rounded-full"
                style={{ background: color, color: 'white' }}
              >
                {TYPE_LABEL[lead.type] ?? lead.type}
              </span>
              <span
                className="text-[11.5px] font-bold px-3 py-0.75 rounded-full"
                style={{ background: STATUS_COLOR[lead.status] ?? '#64748b', color: 'white' }}
              >
                {STATUS_LABEL[lead.status] ?? lead.status}
              </span>
            </div>
          </div>

          {/* ── White floating card ── */}
          <div className="bg-paper rounded-t-3xl -mt-3 px-5 pt-6 pb-8 space-y-6 flex-1">

            {/* Owner Access Request banner */}
            {lead.type === 'seller_interest' && !lead.property_id && (
              <div className="flex items-start gap-3 px-4 py-3.5 rounded-2xl border" style={{ background: '#f5f3ff', borderColor: '#ddd6fe' }}>
                <div className="w-8 h-8 rounded-full shrink-0 grid place-items-center" style={{ background: '#7c3aed18' }}>
                  <KeyRound size={14} style={{ color: '#7c3aed' }} />
                </div>
                <div>
                  <div className="text-[12.5px] font-bold" style={{ color: '#7c3aed' }}>{t('lead_panel.owner_access_title')}</div>
                  <div className="text-[11.5px] text-dim leading-normal mt-0.5">
                    {t('lead_panel.owner_access_desc')}
                  </div>
                </div>
              </div>
            )}

            {/* Contact */}
            <div>
              <div className="text-[10.5px] font-bold uppercase tracking-widest text-dim mb-4">{t('lead_panel.section_contact')}</div>
              <div className="mb-4"><ShareContactButton key={lead.id} contact={{ name: lead.name, email: lead.email, phone: lead.phone }} /></div>
              <div className="space-y-3.5">

                {lead.from_user_name && (
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full grid place-items-center shrink-0" style={{ background: `${color}18` }}>
                      <UserCheck size={13} style={{ color }} />
                    </div>
                    <div>
                      <div className="text-[10.5px] text-dim font-semibold uppercase tracking-wide">{t('lead_panel.label_account')}</div>
                      <div className="text-[13px] text-ink">{lead.from_user_name}</div>
                    </div>
                  </div>
                )}

                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full grid place-items-center shrink-0" style={{ background: `${color}18` }}>
                    <Mail size={13} style={{ color }} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[10.5px] text-dim font-semibold uppercase tracking-wide">{t('lead_panel.label_email')}</div>
                    <div className="flex items-center gap-1.5">
                      <div className="text-[13px] text-ink truncate">{lead.email || t('lead_panel.not_provided')}</div>
                      {lead.email && <button
                        onClick={copyEmail}
                        aria-label={t('lead_panel.copy_email')}
                        className="shrink-0 p-1 rounded-md border-0 bg-transparent cursor-pointer hover:bg-line-soft transition-colors"
                      >
                        {copiedEmail
                          ? <CheckCheck size={13} style={{ color: TONE }} />
                          : <Copy size={13} className="text-dim" />}
                      </button>}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full grid place-items-center shrink-0" style={{ background: `${color}18` }}>
                    <Phone size={13} style={{ color }} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[10.5px] text-dim font-semibold uppercase tracking-wide">{t('lead_panel.label_phone')}</div>
                    {lead.phone ? (() => {
                      const ph = parsePhone(lead.phone)
                      return (
                        <div className="flex items-center gap-1.5">
                          {ph.country && (
                              <img
                                src={`https://flagcdn.com/w20/${ph.country.toLowerCase()}.png`}
                                alt={ph.country}
                                className="w-4 h-auto shrink-0 rounded-sm"
                              />
                            )}
                          <div className="text-[13px] text-ink">{ph.formatted}</div>
                          <button
                            onClick={copyPhone}
                            className="shrink-0 p-1 rounded-md border-0 bg-transparent cursor-pointer hover:bg-line-soft transition-colors"
                          >
                            {copiedPhone
                              ? <CheckCheck size={13} style={{ color: TONE }} />
                              : <Copy size={13} className="text-dim" />}
                          </button>
                        </div>
                      )
                    })() : (
                      <div className="text-[13px] text-dim/60">{t('lead_panel.not_provided')}</div>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full grid place-items-center shrink-0" style={{ background: `${color}18` }}>
                    <Calendar size={13} style={{ color }} />
                  </div>
                  <div>
                    <div className="text-[10.5px] text-dim font-semibold uppercase tracking-wide">{t('lead_panel.label_submitted')}</div>
                    <div className="text-[13px] text-ink">{fmtDate(lead.created_at)}</div>
                  </div>
                </div>

              </div>
            </div>

            <LeadTrackingDetails lead={lead} />
            {onOpenDelivery ? <button type="button" onClick={() => onOpenDelivery(lead.id)} className="min-h-10 rounded-lg border border-line px-4 py-2 text-sm font-semibold text-sea">{t('leads_page.open_delivery')}</button> : null}
            {/* Timeline */}
            <div>
              <div className="text-[10.5px] font-bold uppercase tracking-widest text-dim mb-4">{t('lead_panel.section_timeline')}</div>
              <div className="flex flex-col">
                {[
                  { label: t('lead_panel.label_submitted'),   ts: lead.created_at,   always: true  },
                  { label: t('lead_panel.status_assigned'),   ts: lead.assigned_at,  always: false },
                  { label: t('lead_panel.status_contacted'),  ts: lead.contacted_at, always: false },
                  { label: t('lead_panel.status_closed'),     ts: lead.closed_at,    always: false },
                ].map((step, i, arr) => {
                  const done = !!step.ts
                  const last = i === arr.length - 1
                  return (
                    <div key={step.label} className="flex gap-3">
                      {/* Left: dot + connector */}
                      <div className="flex flex-col items-center" style={{ width: 20 }}>
                        <div
                          className="w-3.5 h-3.5 rounded-full shrink-0 mt-0.5 border-2 transition-colors"
                          style={{
                            background: done ? color : 'transparent',
                            borderColor: done ? color : '#cbd5e1',
                          }}
                        />
                        {!last && (
                          <div
                            className="flex-1 w-px mt-1 mb-1"
                            style={{ background: done ? `${color}50` : '#e2e8f0', minHeight: 20 }}
                          />
                        )}
                      </div>
                      {/* Right: label + timestamp */}
                      <div className={`pb-4 min-w-0 ${last ? '' : ''}`}>
                        <div
                          className="text-[12.5px] font-semibold leading-none mb-0.5"
                          style={{ color: done ? 'var(--ink, #1a1e2e)' : '#94a3b8' }}
                        >
                          {step.label}
                        </div>
                        <div className="text-[11px]" style={{ color: done ? '#64748b' : '#cbd5e1' }}>
                          {step.ts ? fmtDateTime(step.ts) : '—'}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Property */}
            {lead.property_title && (
              <div>
                <div className="text-[10.5px] font-bold uppercase tracking-widest text-dim mb-3">{t('lead_panel.section_property')}</div>
                {(lead.type === 'property_inquiry' || lead.type === 'booking') && lead.property_id ? (
                  <a
                    href={`${LANDING_URL}/detail?id=${lead.property_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2.5 group no-underline"
                  >
                    <div className="w-8 h-8 rounded-full grid place-items-center shrink-0 transition-opacity group-hover:opacity-70" style={{ background: `${color}18` }}>
                      <Home size={13} style={{ color }} />
                    </div>
                    <div className="text-[13px] font-medium flex items-center gap-1.5 group-hover:underline" style={{ color }}>
                      {lead.property_title}
                      <ExternalLink size={11} style={{ color }} className="opacity-60 shrink-0" />
                    </div>
                  </a>
                ) : (
                  <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-full grid place-items-center shrink-0" style={{ background: `${color}18` }}>
                      <Home size={13} style={{ color }} />
                    </div>
                    <div className="text-[13px] text-ink font-medium">{lead.property_title}</div>
                  </div>
                )}
              </div>
            )}

            {/* Message */}
            {lead.message && (
              <div>
                <div className="text-[10.5px] font-bold uppercase tracking-widest text-dim mb-3">{t('lead_panel.section_message')}</div>
                <div className="bg-paper2 rounded-xl px-4 py-3 text-[13px] text-ink2 leading-relaxed whitespace-pre-wrap">
                  {lead.message}
                </div>
              </div>
            )}

            {/* Owner-managed listing — no realtor assignment */}
            {lead.listing_owner_id ? (
              <div className="flex items-center gap-3 px-3.5 py-3 rounded-xl border" style={{ background: '#7c3aed0d', borderColor: '#7c3aed30' }}>
                <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0" style={{ background: '#7c3aed18' }}>
                  <UserCheck size={13} style={{ color: '#7c3aed' }} />
                </div>
                <div className="min-w-0">
                  <div className="text-[11.5px] font-semibold" style={{ color: '#7c3aed' }}>Owner managed</div>
                  <div className="text-[11px] text-dim mt-0.5">
                    This booking is handled directly by <span className="font-medium text-ink2">{lead.listing_owner_name ?? 'the owner'}</span>. No realtor assignment needed.
                  </div>
                </div>
              </div>
            ) : realtors.length > 0 && (
              <div>
                <div className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-widest text-dim mb-3">
                  <UserCheck size={11} />
                  {t('lead_panel.section_assign')}
                </div>

                {/* Quick-assign: property agent (inquiry + booking only) */}
                {(lead.type === 'property_inquiry' || lead.type === 'booking') &&
                  lead.listing_realtor_id &&
                  realtors.some(agent => agent.id === lead.listing_realtor_id) &&
                  lead.listing_realtor_id !== lead.assigned_realtor_id && (
                  <div className="flex items-center justify-between gap-3 px-3 py-2.5 mb-2 rounded-xl border border-line bg-paper2">
                    <div className="min-w-0">
                      <div className="text-[10px] font-bold uppercase tracking-wide text-dim mb-0.5">{t('lead_panel.label_property_agent')}</div>
                      <div className="text-[13px] font-medium text-ink truncate">{lead.listing_realtor_name}</div>
                    </div>
                    <button
                      onClick={() => requestAssign(lead.listing_realtor_id!)}
                      disabled={assignSaving}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border-0 text-[12px] font-semibold text-white cursor-pointer shrink-0 disabled:opacity-50"
                      style={{ background: TONE }}
                    >
                      <Zap size={11} />
                      {t('lead_panel.quick_assign')}
                    </button>
                  </div>
                )}

                <RealtorCombobox
                  realtors={realtors}
                  currentId={lead.assigned_realtor_id}
                  currentLabel={lead.assigned_realtor_name}
                  onSelect={requestAssign}
                  saving={assignSaving}
                  allowUnassign={false}
                />
              </div>
            )}

            {/* Status */}
            <div>
              <div className="text-[10.5px] font-bold uppercase tracking-widest text-dim mb-3">{t('lead_panel.section_status')}</div>
              {lead.listing_owner_id ? (
                <div className="text-[12px] text-dim italic">Status is managed by the owner and cannot be changed here.</div>
              ) : (
                <>
                  <div className="flex gap-2 flex-wrap">
                    {(() => {
                      const currentIndex = STATUSES.indexOf(lead.status as typeof STATUSES[number])
                      return STATUSES.filter(s => !allowedStatuses || allowedStatuses.includes(s)).map(s => {
                        const idx    = STATUSES.indexOf(s)
                        const active = lead.status === s
                        const past   = idx < currentIndex
                        const c      = past ? '#cbd5e1' : STATUS_COLOR[s]
                        return (
                          <button
                            key={s}
                            onClick={() => handleStatus(s)}
                            disabled={statusSaving || past}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-semibold border transition-all"
                            style={{
                              background:  active ? c : 'transparent',
                              color:       active ? 'white' : c,
                              borderColor: c,
                              cursor:      past ? 'default' : 'pointer',
                            }}
                          >
                            {active && <Check size={11} />}
                            {STATUS_LABEL[s]}
                          </button>
                        )
                      })
                    })()}
                  </div>
                  {statusSaving && <div className="text-[11px] text-dim mt-1.5">{t('lead_panel.saving')}</div>}
                </>
              )}
            </div>

          </div>
        </div>

        {/* ── Footer ── */}
        <div className="bg-paper border-t border-line px-5 py-4 shrink-0 flex flex-col gap-2">
          {calendlyUrl && (
            <a
              href={calendlyUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="w-full flex items-center justify-center gap-1.5 px-4 py-2.5 rounded-xl border-0 text-[13px] font-bold text-white no-underline"
              style={{ background: '#006BFF' }}
            >
              <Calendar size={13} />
              Schedule a Session
            </a>
          )}
          <div className="flex gap-2">
            {lead.ghl_contact_url && (
              <a
                href={lead.ghl_contact_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1 flex items-center justify-center gap-1.5 px-4 py-2.5 rounded-xl border border-line text-[13px] font-semibold text-ink2 hover:bg-line-soft no-underline"
              >
                <ExternalLink size={13} />
                {t('lead_panel.open_ghl')}
              </a>
            )}
            <button
              onClick={onClose}
              className={`px-4 py-2.5 rounded-xl border border-line text-[13px] font-semibold text-ink2 cursor-pointer hover:bg-line-soft bg-transparent ${lead.ghl_contact_url ? '' : 'w-full'}`}
            >
              {t('lead_panel.close')}
            </button>
          </div>
        </div>

      </div>
      {/* Owner-access assignment confirmation */}
      {confirmAssignId && (
        <ConfirmModal
          title={t('lead_panel.grant_access_title')}
          description={t('lead_panel.grant_access_desc')}
          confirmLabel={t('lead_panel.grant_access_confirm')}
          variant="warning"
          loading={assignSaving}
          onConfirm={async () => {
            await handleAssign(confirmAssignId)
            setConfirmAssignId(null)
          }}
          onCancel={() => setConfirmAssignId(null)}
        />
      )}

      {/* Skip confirmation modal */}
      {skipConfirm && (
        <div className="fixed inset-0 z-60 flex items-center justify-center p-6" onClick={() => setSkipConfirm(null)}>
          <div
            className="bg-paper rounded-2xl shadow-2xl w-full max-w-xs p-6 flex flex-col gap-4"
            onClick={e => e.stopPropagation()}
          >
            <div>
              <div className="text-[15px] font-bold text-ink mb-1">{t('lead_panel.skip_title')}</div>
              <div className="text-[13px] text-ink2 leading-relaxed">
                {t('lead_panel.skip_body', { skipped: skipConfirm.skipped.join(', '), status: STATUS_LABEL[skipConfirm.status] })}
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setSkipConfirm(null)}
                className="flex-1 px-4 py-2.5 rounded-xl border border-line text-[13px] font-semibold text-ink2 cursor-pointer hover:bg-line-soft bg-transparent"
              >
                {t('lead_panel.cancel')}
              </button>
              <button
                onClick={() => { const s = skipConfirm.status; setSkipConfirm(null); applyStatus(s) }}
                className="flex-1 px-4 py-2.5 rounded-xl border-0 text-[13px] font-semibold text-white cursor-pointer"
                style={{ background: STATUS_COLOR[skipConfirm.status] ?? '#64748b' }}
              >
                {t('lead_panel.yes_skip')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
