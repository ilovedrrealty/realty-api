import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getNetworkProperties, getPrivateProperties, getPrivateProperty } from '../../api/propertyNetwork'
import { getServiceProviders, type ManagedServiceRequest, type ServiceProvider } from '../../api/serviceWorkflow'
import { professionalLabel, type ProfessionalOptions } from '../../../../../packages/professionals/src'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, NetworkFreshness, NetworkPagination, NetworkPanel, networkError, useNetworkDraftGuard } from './NetworkShared'
import { ServiceMutationStatus, useServiceMutation } from './ServiceWorkflowShared'

export function ServiceRequestCreate({options,role,actorCode,onCreated}:{options:ProfessionalOptions;role:string;actorCode?:number|null;onCreated:(id:string)=>void}) {
  const {t,i18n}=useTranslation('property_network')
  const [kind,setKind]=useState<'private'|'listing'>('private'),[sourcePage,setSourcePage]=useState(1),[sourceId,setSourceId]=useState(''),[propertyClass,setPropertyClass]=useState(''),[category,setCategory]=useState(''),[providerPage,setProviderPage]=useState(1),[selected,setSelected]=useState<ServiceProvider[]>([])
  const [title,setTitle]=useState(''),[brief,setBrief]=useState(''),[area,setArea]=useState(''),[alias,setAlias]=useState(''),[desired,setDesired]=useState(''),[ack,setAck]=useState(false),[preview,setPreview]=useState(false)
  const listingRole=['admin','owner','realtor'].includes(role)
  const sources=useNetworkRead('service-source:'+kind+':'+sourcePage,async signal=>{
    if(kind==='private'){const result=await getPrivateProperties({status:'active',page:sourcePage},signal);return {items:result.items.filter(item=>item.can_manage&&item.can_create_project).map(item=>({id:item.id,title:item.title,property_class:item.property_class})),total:result.total,page:result.page,can_create:result.can_create}}
    const result=await getNetworkProperties({page:sourcePage},signal);return {...result,items:result.items.map(item=>({id:item.id,title:item.title,property_class:''})),can_create:true}
  },kind==='private'||listingRole)
  const providers=useNetworkRead('service-providers:'+category+':'+propertyClass+':'+providerPage,signal=>getServiceProviders(category,propertyClass,providerPage,signal),!!category&&!!propertyClass&&!!sourceId)
  async function reauthorize(signal:AbortSignal) {
    if(kind==='private')return getPrivateProperty(sourceId,signal)
    const fresh=await getNetworkProperties({page:sourcePage},signal)
    if(!fresh.items.some(item=>item.id===sourceId))throw new Error('Source authorization could not be reconfirmed')
    return fresh
  }
  const readDenied=[sources.error,providers.error].some(error=>!!error&&networkError(error)==='error_access')
  const mutation=useServiceMutation(reauthorize,value=>{onCreated((value as ManagedServiceRequest).id)},()=>{sources.refresh();providers.refresh();setAck(false);setPreview(false)},readDenied)
  const denied=mutation.denied||readDenied
  const dirty=!!(sourceId||title||brief||area||alias||desired||selected.length||ack)
  useNetworkDraftGuard(dirty||mutation.pending!==null,mutation.busy)
  const source=sources.data?.items.find(item=>item.id===sourceId)
  const fresh=!!sources.data&&!sources.stale&&!sources.error&&!sources.checking&&!!providers.data&&!providers.stale&&!providers.error&&!providers.checking
  const eligible=fresh&&sources.data?.can_create&&!!source&&!!propertyClass&&(kind==='listing'||source.property_class===propertyClass)
  const locked=mutation.busy||mutation.pending!==null
  const valid=eligible&&!!category&&!!title.trim()&&!!brief.trim()&&!!alias.trim()&&selected.length>0&&selected.length<=5
  function chooseSource(id:string){setSourceId(id);setPropertyClass(kind==='private'?sources.data?.items.find(item=>item.id===id)?.property_class??'':'');setSelected([]);setPreview(false);setAck(false)}
  function send(){if(!valid||!preview||!ack||locked)return;mutation.send({operation:'request_create',audience:'managed',input:{submission_id:crypto.randomUUID(),property_id:kind==='private'?sourceId:null,listing_id:kind==='listing'?sourceId:null,property_class:propertyClass,category_id:category,title:title.trim(),brief:brief.trim(),area_label:area.trim(),requester_alias:alias.trim(),desired_start_on:desired||null,recipient_user_codes:selected.map(item=>item.user_code).sort((a,b)=>a-b),shared_brief_acknowledged:true}})}
  if(denied)return <p role="alert">{t('error_access')}</p>
  return <NetworkPanel title={t('service.new_request')}><p className="mb-4 text-sm text-dim">{t('service.unverified')}</p>
    <form className="space-y-5" onSubmit={event=>{event.preventDefault();send()}}><fieldset className="min-w-0 space-y-4" disabled={locked}>
      <label className="block text-sm font-semibold">{t('service.source')}<select className={NETWORK_FIELD} value={kind} onChange={e=>{setKind(e.target.value as typeof kind);setSourcePage(1);chooseSource('')}}><option value="private">{t('registry.private_source')}</option>{listingRole?<option value="listing">{t('registry.listing_source')}</option>:null}</select></label>
      <NetworkFreshness read={sources}/>{sources.error?<p role="alert">{t(networkError(sources.error))}</p>:null}
      <label className="block text-sm font-semibold">{t('service.choose_source')}<select className={NETWORK_FIELD} value={sourceId} required onChange={e=>chooseSource(e.target.value)}><option value="">{t('service.choose_source')}</option>{sources.data?.items.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      {sources.data&&!sources.data.items.length?<p>{t('service.no_sources')}</p>:null}
      {sources.data?<NetworkPagination page={sourcePage} total={sources.data.total} busy={sources.checking} onChange={setSourcePage}/>:null}
      <div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm font-semibold">{t('property_class')}<select className={NETWORK_FIELD} value={propertyClass} disabled={kind==='private'} required onChange={e=>{setPropertyClass(e.target.value);setSelected([]);setProviderPage(1);setPreview(false)}}><option value="">{t('choose_class')}</option>{options.property_classes?.map(option=><option key={option.id} value={option.id}>{professionalLabel(option.label,i18n.language)}</option>)}</select></label>
        <label className="block text-sm font-semibold">{t('service.category')}<select className={NETWORK_FIELD} value={category} required onChange={e=>{setCategory(e.target.value);setSelected([]);setProviderPage(1);setPreview(false)}}><option value="">{t('service.choose_category')}</option>{options.categories.map(option=><option key={option.id} value={option.id}>{professionalLabel(option.label,i18n.language)}</option>)}</select></label></div>
      <NetworkPanel title={t('service.find_members')}><p className="mb-3 text-sm">{t('service.discovery_help')}</p><NetworkFreshness read={providers}/>{providers.error?<p role="alert">{t(networkError(providers.error))}</p>:null}
        {providers.data?.items.map(member=><label className="flex min-h-11 items-start gap-3 py-2 text-sm" key={member.user_code}><input type="checkbox" className="mt-1" checked={selected.some(item=>item.user_code===member.user_code)} disabled={member.user_code===actorCode||selected.length>=5&&!selected.some(item=>item.user_code===member.user_code)} onChange={e=>{if(member.user_code===actorCode)return;setSelected(e.target.checked?[...selected,member]:selected.filter(item=>item.user_code!==member.user_code));setPreview(false);setAck(false)}}/><span className="min-w-0 break-words">{member.member_alias} · {t('service.member_code',{code:member.user_code})}{member.user_code===actorCode?' · '+t('service.your_account'):''}</span></label>)}
        {providers.data&&!providers.data.items.length?<p role="status">{t('service.no_providers')}</p>:null}
        {providers.data?<NetworkPagination page={providerPage} total={providers.data.total} busy={providers.checking} onChange={setProviderPage}/>:null}
        <p className="mt-3 text-xs">{t('service.selected_count',{count:selected.length})}</p><ul>{selected.map(member=><li className="flex items-center justify-between gap-3 text-sm" key={member.user_code}><span className="min-w-0 break-words">{member.member_alias} · {t('service.member_code',{code:member.user_code})}</span><button type="button" className={NETWORK_BUTTON} onClick={()=>{setSelected(selected.filter(item=>item.user_code!==member.user_code));setPreview(false);setAck(false)}}>{t('service.remove')}</button></li>)}</ul>
      </NetworkPanel>
      <label className="block text-sm font-semibold">{t('service.request_title')}<input className={NETWORK_FIELD} required maxLength={180} value={title} onChange={e=>{setTitle(e.target.value);setPreview(false);setAck(false)}}/></label>
      <label className="block text-sm font-semibold">{t('service.requester_alias')}<input className={NETWORK_FIELD} required maxLength={120} value={alias} onChange={e=>{setAlias(e.target.value);setPreview(false);setAck(false)}}/></label>
      <label className="block text-sm font-semibold">{t('service.brief')}<textarea className={NETWORK_FIELD} required maxLength={4000} rows={5} value={brief} onChange={e=>{setBrief(e.target.value);setPreview(false);setAck(false)}}/></label>
      <label className="block text-sm font-semibold">{t('service.coarse_area')}<input className={NETWORK_FIELD} maxLength={120} value={area} onChange={e=>{setArea(e.target.value);setPreview(false);setAck(false)}}/></label>
      <label className="block text-sm font-semibold">{t('service.desired_start')}<input className={NETWORK_FIELD} type="date" value={desired} onChange={e=>{setDesired(e.target.value);setPreview(false);setAck(false)}}/></label><p className="text-xs text-dim">{t('service.not_appointment')}</p>
      <p className="text-sm text-amber-950">{t('service.shared_warning')}</p><button type="button" className={NETWORK_BUTTON} disabled={!valid} onClick={()=>{setPreview(true);setAck(false)}}>{t('service.preview')}</button>
      {preview?<section className="space-y-2 rounded-xl border border-line bg-white p-4" aria-label={t('service.preview')}><h3 className="break-words font-semibold">{title.trim()}</h3><p className="break-words whitespace-pre-wrap">{brief.trim()}</p><p className="break-words">{t('service.requester_alias')}: {alias.trim()}</p><p className="break-words">{t('service.coarse_area')}: {area.trim()||'—'}</p><p>{t('service.desired_start')}: {desired||'—'}</p><p>{t('service.category')}: {professionalLabel(options.categories.find(item=>item.id===category)?.label??{en:category,es:category},i18n.language)}</p><p>{t('property_class')}: {professionalLabel(options.property_classes?.find(item=>item.id===propertyClass)?.label??{en:propertyClass,es:propertyClass},i18n.language)}</p><ul>{selected.map(member=><li className="break-words" key={member.user_code}>{member.member_alias} · {t('service.member_code',{code:member.user_code})}</li>)}</ul><label className="flex min-h-11 items-start gap-3 text-sm"><input className="mt-1" type="checkbox" required checked={ack} onChange={e=>setAck(e.target.checked)}/>{t('service.share_ack')}</label></section>:null}
      {!eligible&&sourceId?<p role="status">{t('service.source_refresh')}</p>:null}<button className={NETWORK_PRIMARY} disabled={!valid||!preview||!ack}>{t('service.send_request')}</button>
    </fieldset><ServiceMutationStatus mutation={mutation}/></form>
  </NetworkPanel>
}
