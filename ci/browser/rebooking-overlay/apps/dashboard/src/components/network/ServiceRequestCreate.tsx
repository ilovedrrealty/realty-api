import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getNetworkProperties, getPrivateProperties, getPrivateProperty } from '../../api/propertyNetwork'
import { getServiceProviders, ServiceContractError, type ManagedServiceRequest, type ServiceProvider } from '../../api/serviceWorkflow'
import { getServiceRebookContext, sameRebookPreview, sameRebookSelection, type ServiceRebookContext } from '../../api/serviceRebook'
import { professionalLabel, type ProfessionalOptions } from '../../../../../packages/professionals/src'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, NetworkFreshness, NetworkPagination, NetworkPanel, networkError, useNetworkDraftGuard } from './NetworkShared'
import { ServiceMutationStatus, useServiceMutation } from './ServiceWorkflowShared'

export function ServiceRequestCreate({options,role,actorCode,rebook,onCreated}:{options:ProfessionalOptions;role:string;actorCode?:number|null;rebook:ServiceRebookContext|null;onCreated:(id:string)=>void}) {
  const {t,i18n}=useTranslation('property_network')
  const titleId=useId()
  const [kind,setKind]=useState<'private'|'listing'>(rebook?.source.kind??'private'),[sourcePage,setSourcePage]=useState(1),[sourceId,setSourceId]=useState(rebook?.source.id??''),[propertyClass,setPropertyClass]=useState(rebook?.source.property_class??''),[category,setCategory]=useState(rebook?.category_id??''),[providerPage,setProviderPage]=useState(1),[selected,setSelected]=useState<ServiceProvider[]>([])
  const [title,setTitle]=useState(''),[brief,setBrief]=useState(''),[area,setArea]=useState(''),[alias,setAlias]=useState(''),[desired,setDesired]=useState(''),[ack,setAck]=useState(false),[preview,setPreview]=useState(false)
  const [rebookPreview, setRebookPreview] = useState<ServiceRebookContext | null>(null)
  const [checkingRepeat, setCheckingRepeat] = useState(false)
  const [repeatError, setRepeatError] = useState('')
  const repeatCheck = useRef<AbortController | null>(null)
  useEffect(() => () => repeatCheck.current?.abort(), [])
  const context = useNetworkRead(`service-rebook-draft:${rebook?.request_id??''}`, signal => getServiceRebookContext(rebook!.request_id, signal), !!rebook)
  const currentRepeat = rebook && context.data && !context.error && sameRebookSelection(rebook, context.data) ? context.data : null
  const repeatFresh = !!currentRepeat && !context.stale && !context.checking
  const reviewCurrent = preview && (!rebook || !!currentRepeat && !!rebookPreview && sameRebookPreview(rebookPreview, currentRepeat))
  const recipients = rebook ? (currentRepeat ? [currentRepeat.provider] : []) : selected
  const categoryLabel = professionalLabel(options.categories.find(item=>item.id===category)?.label??{en:category,es:category},i18n.language)
  const classLabel = professionalLabel(options.property_classes?.find(item=>item.id===propertyClass)?.label??{en:propertyClass,es:propertyClass},i18n.language)
  const listingRole=['admin','owner','realtor'].includes(role)
  const sources=useNetworkRead('service-source:'+kind+':'+sourcePage,async signal=>{
    if(kind==='private'){const result=await getPrivateProperties({status:'active',page:sourcePage},signal);return {items:result.items.filter(item=>item.can_manage&&item.can_create_project).map(item=>({id:item.id,title:item.title,property_class:item.property_class})),total:result.total,page:result.page,can_create:result.can_create}}
    const result=await getNetworkProperties({page:sourcePage},signal);return {...result,items:result.items.map(item=>({id:item.id,title:item.title,property_class:''})),can_create:true}
  },!rebook&&(kind==='private'||listingRole))
  const providers=useNetworkRead('service-providers:'+category+':'+propertyClass+':'+providerPage,signal=>getServiceProviders(category,propertyClass,providerPage,signal),!rebook&&!!category&&!!propertyClass&&!!sourceId)
  async function reauthorize(signal:AbortSignal) {
    if (rebook) {
      const current = await getServiceRebookContext(rebook.request_id, signal)
      if (!sameRebookSelection(rebook, current)) throw new ServiceContractError()
      return current
    }
    if(kind==='private')return getPrivateProperty(sourceId,signal)
    const fresh=await getNetworkProperties({page:sourcePage},signal)
    if(!fresh.items.some(item=>item.id===sourceId))throw new Error('Source authorization could not be reconfirmed')
    return fresh
  }
  const readDenied=[sources.error,providers.error].some(error=>!!error&&networkError(error)==='error_access')
  // A failed rebook refresh must leave an existing creation receipt check available.
  const mutation=useServiceMutation(reauthorize,value=>{onCreated((value as ManagedServiceRequest).id)},()=>{
    if(rebook) context.refresh()
    else { sources.refresh(); providers.refresh() }
    setAck(false)
    setPreview(false)
  },readDenied)
  const denied=mutation.denied||readDenied
  const dirty=!!(title||brief||area||alias||desired||ack||(!rebook&&(sourceId||selected.length)))
  useNetworkDraftGuard(dirty||mutation.pending!==null,mutation.busy||checkingRepeat)
  const source=sources.data?.items.find(item=>item.id===sourceId)
  const fresh=!!sources.data&&!sources.stale&&!sources.error&&!sources.checking&&!!providers.data&&!providers.stale&&!providers.error&&!providers.checking
  const eligible=rebook ? repeatFresh && !!currentRepeat && currentRepeat.provider.user_code !== actorCode
    : fresh&&sources.data?.can_create&&!!source&&!!propertyClass&&(kind==='listing'||source.property_class===propertyClass)
  const locked=mutation.busy||mutation.pending!==null||checkingRepeat
  const valid=eligible&&!!category&&!!title.trim()&&!!brief.trim()&&!!alias.trim()&&recipients.length>0&&recipients.length<=5
  const contextError = context.error ? (networkError(context.error) === 'error_access' ? 'rebook.unavailable' : networkError(context.error))
    : context.data && !currentRepeat ? 'rebook.unavailable' : currentRepeat && rebookPreview && preview && !reviewCurrent ? 'rebook.changed' : repeatError
  function chooseSource(id:string){setSourceId(id);setPropertyClass(kind==='private'?sources.data?.items.find(item=>item.id===id)?.property_class??'':'');setSelected([]);setPreview(false);setAck(false)}
  async function checkRepeat(expected: ServiceRebookContext, apply: () => void) {
    if (repeatCheck.current) return
    const controller = new AbortController()
    repeatCheck.current = controller
    setCheckingRepeat(true)
    setRepeatError('')
    try {
      const latest = await getServiceRebookContext(expected.request_id, controller.signal)
      if (controller.signal.aborted) return
      if (sameRebookPreview(expected, latest)) apply()
      else {
        setRepeatError(sameRebookSelection(expected, latest) ? 'rebook.changed' : 'rebook.unavailable')
        setPreview(false)
        setAck(false)
        context.refresh()
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        const code = networkError(error)
        setRepeatError(code === 'error_access' ? 'rebook.unavailable' : code)
        setPreview(false)
        setAck(false)
        context.refresh()
      }
    } finally {
      if (repeatCheck.current === controller) repeatCheck.current = null
      if (!controller.signal.aborted) setCheckingRepeat(false)
    }
  }
  function send(){
    if(!valid||!reviewCurrent||!ack||locked)return
    const submit = () => mutation.send({operation:'request_create',audience:'managed',input:{submission_id:crypto.randomUUID(),property_id:kind==='private'?sourceId:null,listing_id:kind==='listing'?sourceId:null,property_class:propertyClass,category_id:category,title:title.trim(),brief:brief.trim(),area_label:area.trim(),requester_alias:alias.trim(),desired_start_on:desired||null,recipient_user_codes:recipients.map(item=>item.user_code).sort((a,b)=>a-b),shared_brief_acknowledged:true}})
    if (rebook) { if (currentRepeat) void checkRepeat(currentRepeat, submit) }
    else submit()
  }
  if(denied)return <p role="alert">{t('error_access')}</p>
  return <NetworkPanel title={t(rebook?'rebook.title':'service.new_request')} headingId={rebook?titleId:undefined}><p className="mb-4 text-sm text-dim">{t('service.unverified')}</p>
    <form className="space-y-5" onSubmit={event=>{event.preventDefault();send()}}><fieldset className="min-w-0 space-y-4" disabled={locked}>
      {rebook ? <>
        <NetworkFreshness read={context} />
        {contextError ? <p role="alert" className="text-sm text-red-800">{t(contextError)}</p> : null}
        {currentRepeat ? <dl className="grid gap-3 rounded-xl border border-line bg-white p-4 text-sm sm:grid-cols-2">
          <div><dt className="font-semibold">{t(kind==='private'?'registry.private_source':'registry.listing_source')}</dt><dd className="break-words">{currentRepeat.source.title}</dd></div>
          <div><dt className="font-semibold">{t('property_class')}</dt><dd>{classLabel}</dd></div>
          <div><dt className="font-semibold">{t('service.category')}</dt><dd>{categoryLabel}</dd></div>
          <div><dt className="font-semibold">{t('rebook.provider')}</dt><dd className="break-words">{currentRepeat.provider.member_alias} · {t('service.member_code',{code:currentRepeat.provider.user_code})}</dd></div>
        </dl> : !contextError ? <p role="status">{t('loading')}</p> : null}
      </> : <>
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
      </>}
      <label className="block text-sm font-semibold">{t('service.request_title')}<input className={NETWORK_FIELD} required maxLength={180} value={title} onChange={e=>{setTitle(e.target.value);setPreview(false);setAck(false)}}/></label>
      <label className="block text-sm font-semibold">{t('service.requester_alias')}<input className={NETWORK_FIELD} required maxLength={120} value={alias} onChange={e=>{setAlias(e.target.value);setPreview(false);setAck(false)}}/></label>
      <label className="block text-sm font-semibold">{t('service.brief')}<textarea className={NETWORK_FIELD} required maxLength={4000} rows={5} value={brief} onChange={e=>{setBrief(e.target.value);setPreview(false);setAck(false)}}/></label>
      <label className="block text-sm font-semibold">{t('service.coarse_area')}<input className={NETWORK_FIELD} maxLength={120} value={area} onChange={e=>{setArea(e.target.value);setPreview(false);setAck(false)}}/></label>
      <label className="block text-sm font-semibold">{t('service.desired_start')}<input className={NETWORK_FIELD} type="date" value={desired} onChange={e=>{setDesired(e.target.value);setPreview(false);setAck(false)}}/></label><p className="text-xs text-dim">{t('service.not_appointment')}</p>
      <p className="text-sm text-amber-950">{t('service.shared_warning')}</p><button type="button" className={NETWORK_BUTTON} disabled={!valid} onClick={()=>{setPreview(true);setRebookPreview(currentRepeat);setRepeatError('');setAck(false)}}>{t('service.preview')}</button>
      {reviewCurrent?<section className="space-y-2 rounded-xl border border-line bg-white p-4" aria-label={t('service.preview')}><h3 className="break-words font-semibold">{title.trim()}</h3><p className="break-words whitespace-pre-wrap">{brief.trim()}</p><p className="break-words">{t('service.requester_alias')}: {alias.trim()}</p><p className="break-words">{t('service.coarse_area')}: {area.trim()||'—'}</p><p>{t('service.desired_start')}: {desired||'—'}</p><p>{t('service.category')}: {categoryLabel}</p><p>{t('property_class')}: {classLabel}</p><ul>{recipients.map(member=><li className="break-words" key={member.user_code}>{member.member_alias} · {t('service.member_code',{code:member.user_code})}</li>)}</ul><label className="flex min-h-11 items-start gap-3 text-sm"><input className="mt-1" type="checkbox" required checked={ack} onChange={e=>setAck(e.target.checked)}/>{t('service.share_ack')}</label></section>:null}
      {!rebook&&!eligible&&sourceId?<p role="status">{t('service.source_refresh')}</p>:null}<button className={NETWORK_PRIMARY} disabled={!valid||!reviewCurrent||!ack}>{t('service.send_request')}</button>
    </fieldset><ServiceMutationStatus mutation={rebook ? {...mutation,
      busy: mutation.busy || checkingRepeat,
      checked: mutation.checked && (mutation.conflicted || repeatFresh),
      check: () => { if (!repeatCheck.current) mutation.check() },
      retry: () => { if (currentRepeat && repeatFresh) void checkRepeat(currentRepeat, mutation.retry) },
    } : mutation}/></form>
  </NetworkPanel>
}
