import { useEffect, useState } from 'react'
import { MessageCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Card } from '../../components/dashboard/shared'
import { InquiryContactActions, InquiryHistory } from '../../components/dashboard/InquiryHistory'
import { getOwnerLeads, type InquiryState, type OwnerLeadPage } from '../../api/ownerLeads'
import { inquiryDate } from '../../utils/inquiryFeeds'
import { ShareContactButton } from '../../components/shared/ShareContactDialog'

export function OwnerLeads({ tone, isAdmin = false }: { tone: string; go?: (view: string) => void; isAdmin?: boolean }) {
  const { t, i18n } = useTranslation('inquiry_feeds')
  const [page, setPage] = useState(1)
  const [state, setState] = useState<InquiryState>('all')
  const [data, setData] = useState<OwnerLeadPage | null>(null)
  const [busy, setBusy] = useState(true)
  const [failed, setFailed] = useState(false)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setData(null); setBusy(true); setFailed(false)
    getOwnerLeads(page, state, controller.signal).then(result => { if (!controller.signal.aborted) setData(result) })
      .catch(() => { if (!controller.signal.aborted) setFailed(true) })
      .finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [page, state, refresh])
  return <div className="space-y-5">
    <Card title={<><MessageCircle size={15} />{t('owner_title')}</>}>
      <p className="mb-4 text-sm leading-relaxed text-ink2">{t('current_explanation')}</p>
      {isAdmin ? <p className="mb-4 rounded-lg bg-line-soft p-3 text-xs text-ink2">{t('admin_scope')}</p> : null}
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div><label htmlFor="owner-inquiry-state" className="mb-1 block text-xs font-semibold text-ink2">{t('filter')}</label><select id="owner-inquiry-state" value={state} onChange={event => { setState(event.target.value as InquiryState); setPage(1) }} className="min-h-10 rounded-lg border border-line bg-paper px-3 text-sm text-ink">{(['all', 'open', 'closed'] as const).map(value => <option key={value} value={value}>{t(`filters.${value}`)}</option>)}</select></div>
        <button type="button" disabled={busy} onClick={() => setRefresh(value => value + 1)} className="min-h-10 rounded-lg border border-line px-3 text-sm font-semibold text-ink disabled:opacity-50">{t('refresh')}</button>
      </div>
      {busy ? <p role="status" className="py-6 text-sm text-dim">{t('loading')}</p> : failed ? <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4"><p className="text-sm text-red-900">{t('current_error')}</p><button type="button" className="mt-2 min-h-10 text-sm font-semibold text-sea underline" onClick={() => setRefresh(value => value + 1)}>{t('retry')}</button></div> : data ? <>
        <dl className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3">{(['all_total', 'open_total', 'closed_total'] as const).map(key => <div key={key} className="rounded-lg bg-line-soft p-3"><dt className="text-xs text-dim">{t(`counts.${key}`)}</dt><dd className="mt-1 text-xl font-bold text-ink">{data.summary[key].toLocaleString(i18n.language.startsWith('es') ? 'es-DO' : 'en-US')}</dd></div>)}</dl>
        <p className="mb-4 text-xs text-dim">{t('open_definition')}</p>
        {data.summary.unknown_total > 0 ? <p className="mb-4 text-xs text-dim">{t('unknown_explanation', { count: data.summary.unknown_total })}</p> : null}
        {!data.items.length ? <p className="rounded-lg bg-line-soft p-5 text-sm text-ink2">{t(data.total ? 'empty_page' : 'current_empty')}</p> : <ul className="divide-y divide-line">{data.items.map(lead => <li key={`lead:${lead.id}`} className="space-y-3 py-4 first:pt-0">
          <div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="break-words text-sm font-bold text-ink">{lead.name}</h3><p className="mt-1 break-words text-xs text-dim">{lead.property_title}</p></div><span className="rounded-full bg-line-soft px-2.5 py-1 text-xs font-semibold text-ink">{t(`statuses.${lead.status}`)}</span></div>
          <p className="whitespace-pre-wrap break-words text-sm text-ink2">{lead.message || t('no_message')}</p>
          <p className="break-words text-xs text-ink2">{[lead.phone, lead.email].filter(Boolean).join(' · ') || t('no_contact_action')}</p>
          <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-dim">{inquiryDate(lead.created_at, i18n.language)}</p><InquiryContactActions phone={lead.phone} email={lead.email} tone={tone} /></div>
          <ShareContactButton key={lead.id} contact={{ name: lead.name, email: lead.email, phone: lead.phone }} />
          {lead.assigned_realtor_name ? <p className="text-xs text-dim">{t('assigned_agent', { name: lead.assigned_realtor_name })}</p> : null}
        </li>)}</ul>}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4"><button type="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)} className="min-h-10 rounded-lg border border-line px-3 text-sm font-semibold disabled:opacity-50">{t('previous')}</button><span className="text-xs text-dim">{t('page', { page, pages: Math.max(1, Math.ceil(data.total / data.page_size)), count: data.total })}</span><button type="button" disabled={page * data.page_size >= data.total || page >= 10000} onClick={() => setPage(value => value + 1)} className="min-h-10 rounded-lg border border-line px-3 text-sm font-semibold disabled:opacity-50">{t('next')}</button></div>
      </> : null}
    </Card>
    <InquiryHistory scope="owner" />
  </div>
}
