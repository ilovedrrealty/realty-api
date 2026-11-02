import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getServicePreferences, type ServicePreferences as Preferences } from '../../api/serviceWorkflow'
import { professionalLabel, type ProfessionalOptions } from '../../../../../packages/professionals/src'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { NETWORK_FIELD, NETWORK_PRIMARY, NetworkFreshness, NetworkPanel, networkError, useNetworkDraftGuard } from './NetworkShared'
import { ServiceMutationStatus, serviceDraftEqual, useServiceMutation } from './ServiceWorkflowShared'

type Draft={service_opt_in:boolean;member_alias:string;category_ids:string[];property_class_ids:string[]}
const empty=():Draft=>({service_opt_in:false,member_alias:'',category_ids:[],property_class_ids:[]})
const draftOf=(v:Preferences):Draft=>({service_opt_in:v.service_opt_in,member_alias:v.member_alias,category_ids:v.category_ids,property_class_ids:v.property_class_ids})
export function ServicePreferences({options,actorId}:{options:ProfessionalOptions;actorId:string}) {
  const {t,i18n}=useTranslation('property_network')
  const read=useNetworkRead('service-preferences',getServicePreferences)
  const [baseline,setBaseline]=useState<Preferences|null>(null),[draft,setDraft]=useState<Draft>(empty)
  const dirty=baseline!==null&&!serviceDraftEqual(draft,draftOf(baseline))
  const readDenied=!!read.error&&networkError(read.error)==='error_access'
  const mutation=useServiceMutation(getServicePreferences,value=>{const saved=value as Preferences;setBaseline(saved);setDraft(draftOf(saved));read.refresh()},value=>setBaseline(value as Preferences),readDenied)
  const denied=mutation.denied||readDenied
  useEffect(()=>{
    if(denied){setBaseline(null);setDraft(empty());return}
    if(read.data&&!read.stale&&!read.error&&!mutation.pending&&(!baseline||!dirty&&read.data.version!==baseline.version)){setBaseline(read.data);setDraft(draftOf(read.data))}
  },[denied,read.data,read.stale,read.error,mutation.pending,baseline,dirty])
  useNetworkDraftGuard(dirty||mutation.pending!==null,mutation.busy)
  const fresh=!!read.data&&!read.stale&&!read.error&&!read.checking
  const writable=fresh&&!mutation.busy&&!mutation.pending&&!!read.data?.can_manage&&baseline?.version===read.data.version
  function toggle(key:'category_ids'|'property_class_ids',id:string,checked:boolean){setDraft({...draft,[key]:checked?[...draft[key],id].sort():draft[key].filter(item=>item!==id)})}
  function save(){if(!writable||!baseline)return;mutation.send({operation:'preference_save',audience:'preferences',expected_actor_id:actorId,input:{submission_id:crypto.randomUUID(),expected_version:baseline.version,...draft}})}
  if(denied)return <p role="alert">{t('error_access')}</p>
  return <NetworkPanel title={t('service.preferences')}><p className="mb-3 text-sm">{t('service.opt_in_help')}</p><p className="mb-4 text-xs text-dim">{t('service.unverified')}</p><NetworkFreshness read={read}/>{read.error?<p role="alert">{t(networkError(read.error))}</p>:null}
    {baseline?<form className="space-y-4" onSubmit={event=>{event.preventDefault();save()}}><fieldset className="min-w-0 space-y-4" disabled={!writable}>
      <label className="flex min-h-11 items-start gap-3"><input type="checkbox" className="mt-1" checked={draft.service_opt_in} onChange={e=>setDraft({...draft,service_opt_in:e.target.checked})}/>{t('service.opt_in')}</label>
      <label className="block text-sm font-semibold">{t('service.alias')}<input className={NETWORK_FIELD} maxLength={120} required={draft.service_opt_in} value={draft.member_alias} onChange={e=>setDraft({...draft,member_alias:e.target.value})}/></label>
      <fieldset><legend className="mb-2 text-sm font-semibold">{t('service.categories')}</legend><div className="grid gap-2 sm:grid-cols-2">{options.categories.map(option=><label className="flex min-h-11 items-start gap-2 text-sm" key={option.id}><input className="mt-1" type="checkbox" checked={draft.category_ids.includes(option.id)} disabled={!draft.category_ids.includes(option.id)&&draft.category_ids.length>=12} onChange={e=>toggle('category_ids',option.id,e.target.checked)}/>{professionalLabel(option.label,i18n.language)}</label>)}</div></fieldset>
      <fieldset><legend className="mb-2 text-sm font-semibold">{t('service.classes')}</legend><div className="grid gap-2 sm:grid-cols-2">{options.property_classes?.map(option=><label className="flex min-h-11 items-start gap-2 text-sm" key={option.id}><input className="mt-1" type="checkbox" checked={draft.property_class_ids.includes(option.id)} onChange={e=>toggle('property_class_ids',option.id,e.target.checked)}/>{professionalLabel(option.label,i18n.language)}</label>)}</div></fieldset>
      <p className="text-sm text-dim">{t('service.opt_out_help')}</p><button className={NETWORK_PRIMARY} disabled={!dirty||draft.service_opt_in&&(!draft.member_alias.trim()||!draft.category_ids.length||!draft.property_class_ids.length)}>{t('save_changes')}</button>
    </fieldset>{dirty?<p className="text-xs text-dim">{t('registry.draft_retained')}</p>:null}{read.data&&read.data.version!==baseline.version?<><p role="status">{t('service.changed_review')}</p><button type="button" className={NETWORK_PRIMARY} disabled={!fresh||mutation.busy||mutation.pending!==null} onClick={()=>{if(read.data)setBaseline(read.data)}}>{t('service.review_current')}</button></>:null}<ServiceMutationStatus mutation={mutation}/></form>:!read.error?<p role="status">{t('loading')}</p>:null}
  </NetworkPanel>
}
