import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getServiceRequests, type ServiceRequest } from '../../api/serviceWorkflow'
import type { ProfessionalOptions } from '../../../../../packages/professionals/src'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { NETWORK_BUTTON, NETWORK_PRIMARY, NetworkDate, NetworkFreshness, NetworkPagination, NetworkPanel, allowNetworkNavigation, networkError } from './NetworkShared'
import { ServicePreferences } from './ServicePreferences'
import { ServiceRequestCreate } from './ServiceRequestCreate'
import { ServiceRequestDetail } from './ServiceRequestDetail'

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
  const [tab,setTab]=useState<'managed'|'received'|'preferences'>('managed'),[detail,setDetail]=useState(''),[creating,setCreating]=useState(false)
  const heading=useRef<HTMLHeadingElement|null>(null)
  useEffect(()=>{heading.current?.focus()},[tab,detail,creating])
  function navigate(next:typeof tab){if(allowNetworkNavigation()){setTab(next);setDetail('');setCreating(false)}}
  return <div className="min-w-0 space-y-5"><h2 ref={heading} tabIndex={-1} className="text-xl font-semibold">{t('main.services')}</h2><p className="text-sm text-ink2">{t('service.intro')}</p><nav className="flex flex-wrap gap-2" aria-label={t('service.navigation')}>{(['managed','received','preferences'] as const).map(value=><button key={value} className={tab===value?NETWORK_PRIMARY:NETWORK_BUTTON} aria-current={tab===value?'page':undefined} onClick={()=>navigate(value)}>{t('service.'+value)}</button>)}</nav>
    {tab==='preferences'?<ServicePreferences options={options} actorId={actorId}/>:creating?<><button className={NETWORK_BUTTON} onClick={()=>{if(allowNetworkNavigation())setCreating(false)}}>{t('service.back')}</button><ServiceRequestCreate options={options} role={role} actorCode={actorCode} onCreated={id=>{setCreating(false);setDetail(id)}}/></>:detail?<ServiceRequestDetail key={tab+':'+detail} id={detail} view={tab} role={role} options={options} onBack={()=>setDetail('')} onProject={onProject}/>:<ServiceRequestList key={tab} view={tab} onOpen={setDetail} onCreate={()=>setCreating(true)}/>}
  </div>
}
