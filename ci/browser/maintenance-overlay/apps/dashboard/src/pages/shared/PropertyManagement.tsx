import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Building2, CalendarDays, ClipboardCheck, Download, FileText, ListChecks, Plus, RefreshCw, Search, ShieldCheck, Wallet, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { UserInfo } from '../../lib/auth'
import {
  MANAGEMENT_KINDS, dataText, getManagedProperties, getManagementRecord, getManagementRecords, getManagementStatement,
  managementErrorKey, statementCsv, todayInDR, validStatementPeriod,
  type ManagedProperty, type ManagementKind, type ManagementPage, type ManagementRecord, type ManagementStatement, type ManagementTab,
} from '../../api/propertyManagement'
import { ManagementRecordDialog } from '../../components/management/ManagementRecordDialog'
import { MANAGEMENT_BUTTON, MANAGEMENT_FIELD, MANAGEMENT_PRIMARY, ManagementRecordBadges, managementDate, managementMoney } from '../../components/management/ManagementDisplay'
import { MaintenanceReminders } from '../../components/network/MaintenanceReminders'
import { allowNetworkNavigation } from '../../components/network/NetworkShared'

const ICONS = { overview: Building2, lease: FileText, maintenance: Wrench, inspection: ClipboardCheck, ledger: Wallet, task: ListChecks }
const TABS: ManagementTab[] = ['overview', ...MANAGEMENT_KINDS]

function Panel({ title, description, children, action }: { title: string; description?: string; children: ReactNode; action?: ReactNode }) {
  return <section className="min-w-0 rounded-2xl border border-line bg-paper p-4 sm:p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-bold text-ink">{title}</h3>{description ? <p className="mt-1 text-xs leading-relaxed text-ink2">{description}</p> : null}</div>{action}</div><div className="mt-4">{children}</div></section>
}

function RecordList({ records, onOpen, busy = false, emptyKey = 'empty_records' }: { records: ManagementRecord[]; onOpen: (row: ManagementRecord | string) => void; busy?: boolean; emptyKey?: string }) {
  const { t, i18n } = useTranslation('management')
  const [visible, setVisible] = useState(10)
  if (!records.length) return <p className="rounded-lg bg-line-soft p-4 text-sm leading-relaxed text-ink2">{t(emptyKey)}</p>
  return <><ul className="divide-y divide-line">{records.slice(0, visible).map(row => <li key={row.id} className="flex flex-wrap items-start justify-between gap-3 py-3 first:pt-0"><div className="min-w-0 flex-1"><button type="button" disabled={busy} onClick={() => onOpen(row)} className="break-words text-left text-sm font-bold text-ink underline decoration-transparent hover:decoration-current disabled:opacity-50">{row.title}</button><div className="mt-2"><ManagementRecordBadges record={row} /></div><p className="mt-2 text-xs text-dim">{row.due_date ? `${t('due')}: ${managementDate(row.due_date, i18n.language)} · ` : ''}{t('version', { version: row.version })}{row.kind === 'task' ? ` · ${t(`recurrences.${dataText(row.data, 'recurrence', 'none')}`)}` : ''}</p></div><div className="space-y-2 text-right">{row.amount != null && row.currency ? <div><p className="text-xs text-dim">{t(`fields.${row.kind === 'lease' ? 'rent' : row.kind === 'maintenance' ? 'estimated_cost' : 'amount'}`)}</p><p className="text-sm font-bold text-ink">{managementMoney(row.amount, row.currency, i18n.language)}</p></div> : null}<button type="button" disabled={busy} className={MANAGEMENT_BUTTON} onClick={() => onOpen(row)}>{t('details_edit')}</button></div></li>)}</ul>{visible < records.length ? <button type="button" className={MANAGEMENT_BUTTON + ' mt-3'} onClick={() => setVisible(count => count + 20)}>{t('show_more', { count: records.length - visible })}</button> : null}</>
}

function NativeStatement({ statement, onOpen, busy }: { statement: ManagementStatement; onOpen: (row: ManagementRecord | string) => void; busy: boolean }) {
  const { t, i18n } = useTranslation('management')
  const [rentVisible, setRentVisible] = useState(12)
  const [entriesVisible, setEntriesVisible] = useState(20)
  const exportCsv = () => {
    const labels: Record<string, string> = {}
    for (const key of ['property', 'location', 'period', 'manual_note', 'income', 'expenses', 'net', 'deposit_cash', 'deposit_balance', 'currency', 'title', 'date', 'direction', 'category', 'amount', 'reference', 'lease', 'rent_period']) labels[key] = t(`csv.${key}`)
    const content = statementCsv(statement, labels)
    const url = URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `property-statement-${statement.period.start}-${statement.period.end}.csv`
    document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0)
  }
  return <div className="space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-sea/20 bg-sea/5 p-4"><div><h3 className="text-sm font-bold text-ink">{t('statement_title')}</h3><p className="mt-1 text-xs text-ink2">{t('statement_range', { start: managementDate(statement.period.start, i18n.language), end: managementDate(statement.period.end, i18n.language), asOf: managementDate(statement.period.as_of, i18n.language) })}</p><p className="mt-2 max-w-3xl text-xs leading-relaxed text-ink2">{t('manual_note')}</p></div><button type="button" className={MANAGEMENT_BUTTON + ' inline-flex items-center gap-2'} onClick={exportCsv}><Download size={16} aria-hidden="true" />{t('export_statement')}</button></div>
    <div className="grid gap-4 xl:grid-cols-2">{(['USD', 'DOP'] as const).map(currency => {
      const total = statement.totals[currency]
      return total ? <Panel key={currency} title={`${currency} · ${t(`currencies.${currency}`)}`} description={t('native_currency_note')}><dl className="grid grid-cols-2 gap-4">{(['income', 'expenses', 'net', 'deposit_cash', 'deposit_balance'] as const).map(key => <div key={key} className={key === 'net' ? 'col-span-2 rounded-xl bg-line-soft p-3' : ''}><dt className="text-xs text-ink2">{t(`totals.${key}`)}</dt><dd className={`mt-1 break-words font-bold text-ink ${key === 'net' ? 'text-2xl' : 'text-lg'}`}>{managementMoney(total[key], currency, i18n.language)}</dd></div>)}</dl><p className="mt-4 text-xs leading-relaxed text-dim">{t('deposit_statement_note')}</p></Panel> : <Panel key={currency} title={`${currency} · ${t(`currencies.${currency}`)}`}><p className="text-sm leading-relaxed text-dim">{t('empty_currency')}</p></Panel>
    })}</div>
    <Panel title={t('rent_schedule')} description={t('rent_schedule_note')}>
      {statement.rent_due.length ? <><div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm"><caption className="sr-only">{t('rent_schedule_note')}</caption><thead><tr className="border-b border-line text-xs text-ink2">{['lease', 'due', 'expected', 'received', 'outstanding'].map(key => <th key={key} scope="col" className="px-2 pb-3 font-semibold">{t(key)}</th>)}</tr></thead><tbody>{statement.rent_due.slice(0, rentVisible).map(row => <tr key={`${row.lease_id}:${row.due_date}`} className="border-b border-line last:border-0"><td className="max-w-64 break-words p-2"><button type="button" className="font-semibold text-ink underline" disabled={busy} onClick={() => onOpen(row.lease_id)}>{row.title}</button></td><td className="p-2">{managementDate(row.due_date, i18n.language)}{row.overdue ? <span className="mt-1 block text-xs font-bold text-red-800">{t('overdue')}</span> : null}</td><td className="whitespace-nowrap p-2">{managementMoney(row.expected, row.currency, i18n.language)}</td><td className="whitespace-nowrap p-2">{managementMoney(row.recorded_received, row.currency, i18n.language)}</td><td className="whitespace-nowrap p-2 font-semibold">{managementMoney(row.outstanding, row.currency, i18n.language)}</td></tr>)}</tbody></table></div>{rentVisible < statement.rent_due.length ? <button type="button" className={MANAGEMENT_BUTTON + ' mt-3'} onClick={() => setRentVisible(count => count + 30)}>{t('show_more', { count: statement.rent_due.length - rentVisible })}</button> : null}</> : <p className="text-sm text-dim">{t('empty_rent_schedule')}</p>}
    </Panel>
    <div className="grid gap-5 xl:grid-cols-2"><Panel title={t('open_work')} description={t('maintenance_note')}><RecordList records={statement.open_work_orders} onOpen={onOpen} busy={busy} emptyKey="empty_open_work" /></Panel><Panel title={t('upcoming_tasks')} description={t('upcoming_tasks_note')}><RecordList records={statement.due_items} onOpen={onOpen} busy={busy} emptyKey="empty_upcoming" /></Panel></div>
    <Panel title={t('lease_renewals')} description={t('renewals_note')}><RecordList records={statement.renewals} onOpen={onOpen} busy={busy} emptyKey="empty_renewals" /></Panel>
    <Panel title={t('recorded_entries')} description={t('recorded_entries_note')}>
      {statement.entries.length ? <><div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm"><caption className="sr-only">{t('recorded_entries_note')}</caption><thead><tr className="border-b border-line text-xs text-ink2">{['date', 'title', 'direction', 'category', 'amount'].map(key => <th scope="col" key={key} className="px-2 pb-3 font-semibold">{t(`csv.${key}`)}</th>)}</tr></thead><tbody>{statement.entries.slice(0, entriesVisible).map(row => <tr key={row.id} className="border-b border-line last:border-0"><td className="whitespace-nowrap p-2">{managementDate(dataText(row.data, 'transaction_date'), i18n.language)}</td><td className="max-w-64 break-words p-2"><button type="button" className="font-semibold underline" disabled={busy} onClick={() => onOpen(row)}>{row.title}</button></td><td className="p-2">{t(`directions.${dataText(row.data, 'direction')}`)}</td><td className="p-2">{t(`ledger_categories.${dataText(row.data, 'category')}`)}</td><td className="whitespace-nowrap p-2 font-semibold">{managementMoney(dataText(row.data, 'amount'), dataText(row.data, 'currency'), i18n.language)}</td></tr>)}</tbody></table></div>{entriesVisible < statement.entries.length ? <button type="button" className={MANAGEMENT_BUTTON + ' mt-3'} onClick={() => setEntriesVisible(count => count + 50)}>{t('show_more', { count: statement.entries.length - entriesVisible })}</button> : null}</> : <p className="text-sm text-dim">{t('empty_entries')}</p>}
    </Panel>
  </div>
}

function PropertyWorkspace({ property, actorRole }: { property: ManagedProperty; actorRole: string }) {
  const { t } = useTranslation('management')
  const today = todayInDR()
  const [tab, setTab] = useState<ManagementTab>('overview')
  const [rangeDraft, setRangeDraft] = useState({ start: today.slice(0, 8) + '01', end: today })
  const [range, setRange] = useState(rangeDraft)
  const [periodError, setPeriodError] = useState(false)
  const [statement, setStatement] = useState<ManagementStatement | null>(null)
  const [statementBusy, setStatementBusy] = useState(true)
  const [statementError, setStatementError] = useState<string | null>(null)
  const [recordPage, setRecordPage] = useState(1)
  const [records, setRecords] = useState<ManagementPage<ManagementRecord> | null>(null)
  const [recordsBusy, setRecordsBusy] = useState(false)
  const [recordsError, setRecordsError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [opening, setOpening] = useState(false)
  const [openError, setOpenError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<{ kind: ManagementKind; record: ManagementRecord | null; key: string } | null>(null)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    const controller = new AbortController(); let active = true
    setStatementBusy(true); setStatementError(null); setStatement(null)
    getManagementStatement(property.id, range.start, range.end, controller.signal).then(result => { if (active) setStatement(result) })
      .catch(reason => { if (active) setStatementError(managementErrorKey(reason)) }).finally(() => { if (active) setStatementBusy(false) })
    return () => { active = false; controller.abort() }
  }, [property.id, range.start, range.end, refresh])
  useEffect(() => {
    if (tab === 'overview') return
    const controller = new AbortController(); let active = true
    setRecordsBusy(true); setRecordsError(null); setRecords(null)
    getManagementRecords(property.id, tab, recordPage, controller.signal).then(result => { if (active) setRecords(result) })
      .catch(reason => { if (active) setRecordsError(managementErrorKey(reason)) }).finally(() => { if (active) setRecordsBusy(false) })
    return () => { active = false; controller.abort() }
  }, [property.id, tab, recordPage, refresh])
  const openRecord = async (row: ManagementRecord | string) => {
    if (opening) return
    setOpenError(null); setOpening(true)
    try {
      const record = await getManagementRecord(typeof row === 'string' ? row : row.id)
      if (alive.current && record.listing_id === property.id) setDialog({ kind: record.kind, record, key: record.id })
    } catch (reason) { if (alive.current) setOpenError(managementErrorKey(reason)) }
    finally { if (alive.current) setOpening(false) }
  }
  const applyPeriod = (event: FormEvent) => { event.preventDefault(); const valid = validStatementPeriod(rangeDraft.start, rangeDraft.end); setPeriodError(!valid); if (valid) setRange({ ...rangeDraft }) }
  const canUseReminders = actorRole !== 'admin' && property.status === 'active' && property.can_approve
    && statementError !== 'error_access' && statementError !== 'error_missing' && statement?.can_approve !== false
  return <div className="space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-wider text-dim">{t('selected_property')}</p><h2 className="mt-1 text-xl font-bold text-ink">{property.title}</h2><p className="mt-1 text-sm text-ink2">{property.location}</p></div><button type="button" className={MANAGEMENT_BUTTON + ' inline-flex items-center gap-2'} disabled={statementBusy || recordsBusy} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={16} aria-hidden="true" />{t('refresh')}</button></div>
    <nav className="flex flex-wrap gap-2 rounded-2xl border border-line bg-paper p-2" aria-label={t('workspace_tabs')}>{TABS.map(key => { const Icon = ICONS[key]; return <button type="button" key={key} aria-pressed={key === tab} onClick={() => { if (key !== tab && !allowNetworkNavigation()) return; setTab(key); setRecordPage(1); setOpenError(null) }} className={`inline-flex min-h-11 items-center gap-2 rounded-xl px-3 py-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-sea ${key === tab ? 'bg-sea text-white' : 'text-ink2 hover:bg-line-soft'}`}><Icon size={17} aria-hidden="true" />{t(`tabs.${key}`)}</button> })}</nav>
    <div className="rounded-xl border border-line bg-paper p-4"><div className="flex flex-wrap items-end justify-between gap-4"><form className="flex flex-wrap items-end gap-3" onSubmit={applyPeriod}><label className="text-xs font-semibold text-ink2">{t('period_start')}<input type="date" required className={MANAGEMENT_FIELD + ' mt-1'} value={rangeDraft.start} onChange={event => setRangeDraft(previous => ({ ...previous, start: event.target.value }))} /></label><label className="text-xs font-semibold text-ink2">{t('period_end')}<input type="date" required min={rangeDraft.start} className={MANAGEMENT_FIELD + ' mt-1'} value={rangeDraft.end} onChange={event => setRangeDraft(previous => ({ ...previous, end: event.target.value }))} /></label><button type="submit" className={MANAGEMENT_BUTTON}>{t('apply_period')}</button></form>{tab !== 'overview' ? <button type="button" className={MANAGEMENT_PRIMARY + ' inline-flex items-center gap-2'} onClick={() => setDialog({ kind: tab, record: null, key: `new:${tab}` })}><Plus size={17} aria-hidden="true" />{t('add_kind', { kind: t(`kinds.${tab}`) })}</button> : null}</div><p className="mt-2 text-xs text-dim">{t('period_filter_note')}</p>{periodError ? <p role="alert" className="mt-2 text-sm text-red-800">{t('error_period')}</p> : null}</div>
    {openError ? <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">{t(openError)}</p> : null}{opening ? <p role="status" className="text-sm text-dim">{t('opening_record')}</p> : null}
    {statementError ? <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">{t(statementError)}</p> : null}
    {tab === 'overview' ? <>
      {statementBusy ? <div role="status" className="rounded-2xl border border-line bg-paper p-8 text-sm text-dim">{t('loading_statement')}</div> : null}
      {statement ? <><div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">{MANAGEMENT_KINDS.map(key => { const Icon = ICONS[key]; return <button key={key} type="button" className="rounded-xl border border-line bg-paper p-4 text-left hover:border-sea focus-visible:outline-2 focus-visible:outline-sea" onClick={() => { setTab(key); setRecordPage(1) }}><Icon size={20} className="text-sea" aria-hidden="true" /><span className="mt-3 block text-2xl font-bold text-ink">{statement.counts[key].toLocaleString()}</span><span className="mt-1 block text-xs text-ink2">{t(`tabs.${key}`)}</span></button> })}</div><p className="text-xs text-dim">{t('record_counts_note')}</p><NativeStatement key={`${property.id}:${range.start}:${range.end}:${refresh}`} statement={statement} onOpen={openRecord} busy={opening} /></> : null}
    </> : <Panel title={t(`tabs.${tab}`)} description={t(`kind_notes.${tab}`)} action={<span className="text-xs text-dim">{records ? t('records_total', { count: records.total }) : ''}</span>}>
      {recordsBusy ? <p role="status" className="py-8 text-sm text-dim">{t('loading')}</p> : null}{recordsError ? <p role="alert" className="text-sm text-red-800">{t(recordsError)}</p> : null}
      {records ? <><RecordList key={`${tab}:${recordPage}:${refresh}`} records={records.items} onOpen={openRecord} busy={opening} /><div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4"><button type="button" className={MANAGEMENT_BUTTON} disabled={recordsBusy || recordPage <= 1} onClick={() => setRecordPage(page => page - 1)}>{t('previous')}</button><span className="text-xs text-dim">{t('page_number', { page: records.page, total: Math.max(1, Math.ceil(records.total / records.page_size)) })}</span><button type="button" className={MANAGEMENT_BUTTON} disabled={recordsBusy || recordPage * records.page_size >= records.total} onClick={() => setRecordPage(page => page + 1)}>{t('next')}</button></div></> : null}
    </Panel>}
    {tab === 'maintenance' && canUseReminders ? <MaintenanceReminders source={{ kind: 'listing', id: property.id }} /> : null}
    {dialog ? <ManagementRecordDialog key={dialog.key} listingId={property.id} kind={dialog.kind} record={dialog.record} canApprove={statement?.can_approve ?? property.can_approve} onClose={() => setDialog(null)} onSaved={() => setRefresh(value => value + 1)} /> : null}
  </div>
}

function ManagementWorkspace({ actorRole }: { actorRole: string }) {
  const { t } = useTranslation('management')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [properties, setProperties] = useState<ManagementPage<ManagedProperty> | null>(null)
  const [selected, setSelected] = useState<ManagedProperty | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController(); let active = true
    setBusy(true); setError(null); setProperties(null)
    getManagedProperties(query, page, controller.signal).then(result => {
      if (active) { setProperties(result); setSelected(previous => previous ? result.items.find(item => item.id === previous.id) ?? previous : result.items[0] ?? null) }
    }).catch(reason => { if (active) setError(managementErrorKey(reason)) }).finally(() => { if (active) setBusy(false) })
    return () => { active = false; controller.abort() }
  }, [query, page, refresh])
  const find = (event: FormEvent) => { event.preventDefault(); setPage(1); setQuery(search.trim()); setRefresh(value => value + 1) }
  return <div className="space-y-6">
    <header><div className="mb-3 inline-flex items-center gap-2 rounded-full bg-sea/10 px-3 py-1.5 text-xs font-bold text-sea"><ShieldCheck size={15} aria-hidden="true" />{t('private_workspace')}</div><h1 className="text-2xl font-bold text-ink sm:text-3xl">{t('title')}</h1><p className="mt-2 max-w-4xl text-sm leading-relaxed text-ink2">{t('subtitle')}</p></header>
    <Panel title={t('choose_property')} description={t('scope_note')}>
      <form className="flex flex-wrap gap-3" onSubmit={find}><label className="min-w-0 flex-1"><span className="sr-only">{t('search_properties')}</span><input type="search" className={MANAGEMENT_FIELD} maxLength={160} value={search} placeholder={t('search_placeholder')} onChange={event => setSearch(event.target.value)} /></label><button type="submit" className={MANAGEMENT_BUTTON + ' inline-flex items-center gap-2'} disabled={busy}><Search size={16} aria-hidden="true" />{t('search')}</button></form>
      {busy ? <p role="status" className="mt-4 text-sm text-dim">{t('loading_properties')}</p> : null}{error ? <div role="alert" className="mt-4 text-sm text-red-800"><p>{t(error)}</p><button type="button" className={MANAGEMENT_BUTTON + ' mt-2'} onClick={() => setRefresh(value => value + 1)}>{t('refresh')}</button></div> : null}
      {properties ? <><label className="mt-4 block text-xs font-semibold text-ink2">{t('choose_property')}<select className={MANAGEMENT_FIELD + ' mt-1'} value={selected?.id ?? ''} onChange={event => { const property = properties.items.find(row => row.id === event.target.value); if (property && allowNetworkNavigation()) setSelected(property) }}><option value="" disabled>{t('choose_property')}</option>{selected && !properties.items.some(row => row.id === selected.id) ? <option value={selected.id}>{selected.title} · {selected.location}</option> : null}{properties.items.map(property => <option key={property.id} value={property.id}>{property.title} · {property.location}</option>)}</select></label>{!properties.items.length ? <p className="mt-4 rounded-xl bg-line-soft p-4 text-sm text-ink2">{t(query ? 'empty_property_search' : 'empty_properties')}</p> : null}<div className="mt-4 flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-dim">{t('properties_total', { count: properties.total })}</p>{properties.total > properties.page_size ? <div className="flex items-center gap-3"><button type="button" className={MANAGEMENT_BUTTON} disabled={busy || page <= 1} onClick={() => setPage(value => value - 1)}>{t('previous')}</button><span className="text-xs text-dim">{t('page_number', { page: properties.page, total: Math.ceil(properties.total / properties.page_size) })}</span><button type="button" className={MANAGEMENT_BUTTON} disabled={busy || page * properties.page_size >= properties.total} onClick={() => setPage(value => value + 1)}>{t('next')}</button></div> : null}</div></> : null}
    </Panel>
    {selected && error !== 'error_access' ? <PropertyWorkspace key={selected.id} property={selected} actorRole={actorRole} /> : !busy && !error ? <div className="flex items-start gap-3 rounded-2xl border border-line bg-paper p-6 text-sm text-ink2"><CalendarDays size={22} className="shrink-0 text-sea" aria-hidden="true" /><p>{t('select_property_hint')}</p></div> : null}
  </div>
}

export function PropertyManagement({ user }: { user: UserInfo }) {
  const { t } = useTranslation('management')
  if (!['admin', 'owner', 'realtor'].includes(user.role)) return <div role="alert" className="rounded-xl border border-line bg-paper p-6 text-sm text-ink2">{t('error_access')}</div>
  return <ManagementWorkspace key={`${user.id}:${user.role}`} actorRole={user.role} />
}
