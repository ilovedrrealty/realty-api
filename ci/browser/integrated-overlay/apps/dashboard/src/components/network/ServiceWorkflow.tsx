import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getServiceRequests, type ServiceRequest } from '../../api/serviceWorkflow'
import type { ServiceRebookContext } from '../../api/serviceRebook'
import type { ServiceOperationsScope, ServiceOperationsView } from '../../api/serviceOperations'
import type { ProfessionalOptions } from '../../../../../packages/professionals/src'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { NETWORK_BUTTON, NETWORK_PRIMARY, NetworkDate, NetworkFreshness, NetworkPagination, NetworkPanel, allowNetworkNavigation, networkError } from './NetworkShared'
import { ServicePreferences } from './ServicePreferences'
import { ServiceRequestCreate } from './ServiceRequestCreate'
import { ServiceRequestDetail } from './ServiceRequestDetail'
import { ServiceFavorites } from './ServiceFavorites'
import { ServiceOperations } from './ServiceOperations'

const SERVICE_TABS = ['managed', 'received', 'preferences'] as const
type ServiceTab = typeof SERVICE_TABS[number] | 'favorites' | 'operations'
type OpenRequest = { id: string; view: ServiceOperationsView; tab: ServiceTab }

function ServiceRequestList({view,onOpen,onCreate}:{view:'managed'|'received';onOpen:(id:string)=>void;onCreate:()=>void}) {
  const {t}=useTranslation('property_network')
  const [status,setStatus]=useState('all'),[page,setPage]=useState(1)
  const read=useNetworkRead('service-requests:'+view+':'+status+':'+page,signal=>getServiceRequests(view,status,page,signal))
  const states=view==='managed'?['all','open','awarded','cancelled']:['all','actionable','closed','awarded']
  const denied=!!read.error&&networkError(read.error)==='error_access'
  return <NetworkPanel title={t(view==='managed'?'service.managed':'service.received')}><div className="mb-4 flex flex-wrap items-end justify-between gap-3"><label className="text-sm font-semibold">{t('service.filter')}<select className="ml-2 min-h-11 rounded-lg border border-line px-3" value={status} onChange={e=>{setStatus(e.target.value);setPage(1)}}>{states.map(value=><option key={value} value={value}>{t('service.filters.'+value)}</option>)}</select></label>{view==='managed'&&read.data?.can_create&&!denied?<button className={NETWORK_PRIMARY} onClick={onCreate}>{t('service.new_request')}</button>:null}</div><NetworkFreshness read={read}/>
    {read.error?<p role="alert">{t(networkError(read.error))}</p>:null}{!denied&&read.data?<><div className="grid gap-4 md:grid-cols-2">{read.data.items.map((item:ServiceRequest)=><article className="min-w-0 space-y-3 rounded-xl border border-line bg-white p-4" key={item.id}><p className="text-xs text-dim">{item.view==='managed'?t('service.request_states.'+item.status):t('service.recipient_states.'+item.recipient.status)}</p><h3 className="break-words font-semibold">{item.title}</h3><p className="text-xs"><NetworkDate value={item.created_at}/></p><button className={NETWORK_BUTTON} onClick={()=>onOpen(item.id)}>{t('service.open_request')}</button></article>)}</div>{!read.data.items.length?<p role="status">{t('service.empty_requests')}</p>:null}<NetworkPagination page={page} total={read.data.total} busy={read.checking} onChange={setPage}/></>:!read.error?<p role="status">{t('loading')}</p>:null}
  </NetworkPanel>
}
export function ServiceWorkflow({options,role,actorId,actorCode,onProject}:{options:ProfessionalOptions;role:string;actorId:string;actorCode:number|null;onProject:(id:string)=>void}) {
  const {t}=useTranslation('property_network')
  const [selectedTab, setTab] = useState<ServiceTab>('managed'), [detail, setDetail] = useState<OpenRequest | null>(null)
  const [draft, setDraft] = useState<'new' | ServiceRebookContext | null>(null)
  const [operationsScope, setOperationsScope] = useState<ServiceOperationsScope>({ view: 'managed', page: 1 })
  const tabs: readonly ServiceTab[] = role === 'admin' ? SERVICE_TABS : [...SERVICE_TABS, 'favorites', 'operations']
  const tab = tabs.includes(selectedTab) ? selectedTab : 'managed'
  const currentDetail = detail?.tab === tab ? detail : null
  const currentDraft = tab === selectedTab ? draft : null
  const rebook = currentDraft === 'new' ? null : currentDraft
  const heading=useRef<HTMLHeadingElement|null>(null)
  useEffect(()=>{heading.current?.focus()},[tab,currentDetail,currentDraft])
  function navigate(next:ServiceTab){if(allowNetworkNavigation()){setTab(next);setDetail(null);setDraft(null)}}
  function closeDraft() { if (allowNetworkNavigation()) setDraft(null) }
  function openRequest(id: string, view: ServiceOperationsView) { setDraft(null); setDetail({ id, view, tab }) }
  return <div className="min-w-0 space-y-5"><h2 ref={heading} tabIndex={-1} className="text-xl font-semibold">{t('main.services')}</h2><p className="text-sm text-ink2">{t('service.intro')}</p><nav className="flex flex-wrap gap-2" aria-label={t('service.navigation')}>{tabs.map(value=><button key={value} className={tab===value?NETWORK_PRIMARY:NETWORK_BUTTON} aria-current={tab===value?'page':undefined} onClick={()=>navigate(value)}>{t(value==='favorites'?'favorites.title':value==='operations'?'operations.title':'service.'+value)}</button>)}</nav>
    {tab==='favorites'?<ServiceFavorites key={actorId} actorId={actorId}/>:tab==='preferences'?<ServicePreferences options={options} actorId={actorId}/>:currentDraft?<>
      <button className={NETWORK_BUTTON} onClick={closeDraft}>{t(rebook?'rebook.back':'service.back')}</button>
      <ServiceRequestCreate key={rebook?.request_id??'new'} options={options} role={role} actorCode={actorCode} rebook={rebook} onCreated={id=>openRequest(id,'managed')}/>
    </>:currentDetail?<ServiceRequestDetail key={`${tab}:${currentDetail.view}:${currentDetail.id}`} id={currentDetail.id}
      view={currentDetail.view} role={role} options={options} onBack={()=>setDetail(null)} onProject={onProject} onRebook={setDraft}
      backLabel={tab==='operations'?t('operations.back'):undefined}/>:tab==='operations'?
      <ServiceOperations actorId={actorId} scope={operationsScope} onScope={setOperationsScope} onOpen={openRequest}/>:<ServiceRequestList key={tab} view={tab} onOpen={id=>openRequest(id,tab)} onCreate={()=>setDraft('new')}/>}
  </div>
}
