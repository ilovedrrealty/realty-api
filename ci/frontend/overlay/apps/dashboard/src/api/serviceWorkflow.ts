import client from './axios'
import { PRIVATE_PROPERTY_CLASSES, type NetworkPage } from './propertyNetwork'

const PREFIX = '/property-network'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export const SERVICE_OPERATIONS = ['preference_save', 'request_create', 'recipient_accept', 'recipient_decline', 'recipient_revoke', 'provider_quote', 'owner_counter', 'counter_accept', 'counter_decline', 'proposal_withdraw', 'request_cancel', 'request_award'] as const
export type ServiceOperation = typeof SERVICE_OPERATIONS[number]
export type ServiceAudience = 'preferences' | 'managed' | 'received'
export type ServicePreferences = { service_opt_in: boolean; member_alias: string; category_ids: string[]; property_class_ids: string[]; version: number; updated_at: string | null; can_manage: boolean }
export type ServiceProvider = { user_code: number; member_alias: string; category_ids: string[] }
export type ProposalTerms = { amount: string; currency: 'USD' | 'DOP'; pricing_basis: 'fixed' | 'estimate'; scope_text: string; exclusions: string; estimated_days: number | null; valid_until: string }
export type ServiceProposal = ProposalTerms & { id: string; recipient_id: string; revision_no: number; parent_proposal_id: string | null; kind: 'provider_quote' | 'owner_counter'; status: 'submitted' | 'accepted' | 'declined' | 'withdrawn' | 'superseded' | 'closed' | 'awarded'; version: number; is_expired: boolean; created_at: string }
export type ManagedRecipient = { id: string; user_code: number; member_alias: string; status: string; version: number; current_proposal: ServiceProposal | null; capabilities: { can_revoke: boolean; can_counter: boolean; can_award: boolean; can_withdraw: boolean } }
type SharedRequest = { id: string; category_id: string; property_class?: string; title: string; brief: string; area_label: string; requester_alias: string; desired_start_on: string | null; created_at: string; project_id: string | null; can_open_project: boolean }
export type ManagedServiceRequest = SharedRequest & { view: 'managed'; version: number; status: 'open' | 'awarded' | 'cancelled'; source: { property_id: string | null; listing_id: string | null; title: string; property_class: string }; updated_at: string; recipients: ManagedRecipient[]; capabilities: { can_cancel: boolean; can_compare: boolean; can_award: boolean } }
export type ReceivedServiceRequest = SharedRequest & { view: 'received'; property_class: string; recipient: { id: string; status: string; version: number }; current_proposal: ServiceProposal | null; capabilities: { can_accept: boolean; can_decline: boolean; can_quote: boolean; can_withdraw: boolean; can_accept_counter: boolean; can_decline_counter: boolean } }
export type ServiceRequest = ManagedServiceRequest | ReceivedServiceRequest
// Deliberately retain only the authorized project link here; the existing project
// screen fetches/validates its full ProjectView through its current project ACL.
export type ServiceAward = { request: ManagedServiceRequest; project: { id: string } | null }
export type ServiceCurrent = ServicePreferences | ServiceRequest | ServiceAward
export type ServiceAction = { operation: ServiceOperation; audience: ServiceAudience; expected_actor_id?: string; request_id?: string; recipient_id?: string; proposal_id?: string; input: { submission_id: string; [key: string]: unknown } }
export type ServiceSubmission = { submission_id: string; operation: ServiceOperation; target_id: string; applied_at: string; result_versions: Record<string, number>; current: ServiceCurrent }
export type ServiceEvent = { id: string; action: string; occurred_at: string; recipient_id: string | null; proposal_id: string | null; result_versions: Record<string, number> }
export type ServiceComparison = { request_id: string; request_version: number; groups: { currency: 'USD' | 'DOP'; items: ManagedRecipient[] }[]; unquoted: ManagedRecipient[] }

export class ServiceContractError extends Error {
  constructor() { super('Invalid private service data. Refresh and review.'); this.name = 'ServiceContractError' }
}
// Created only by synchronous preparation, before any authenticated transport.
// Response decoding continues to use ServiceContractError and stays uncertain.
export class ServiceInputError extends ServiceContractError {
  constructor() { super(); this.name = 'ServiceInputError' }
}
function invalid(): never { throw new ServiceContractError() }
function object(value: unknown): Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function exact(value: unknown, keys: readonly string[]) { const row = object(value); if (Object.keys(row).some(key => !keys.includes(key))) invalid(); return row }
function text(value: unknown, max: number, min = 0): string { return typeof value === 'string' && [...value].length >= min && [...value].length <= max && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value) ? value : invalid() }
function int(value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= min && value <= max ? value : invalid() }
function bool(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid() }
function uuid(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value.toLowerCase() : invalid() }
function nullableId(value: unknown) { return value === null ? null : uuid(value) }
function choice<T extends string>(value: unknown, values: readonly T[]): T { return typeof value === 'string' && values.includes(value as T) ? value as T : invalid() }
function slug(value: unknown) { const result = text(value, 80, 1); return /^[a-z][a-z0-9_]*$/.test(result) ? result : invalid() }
function list<T>(value: unknown, max: number, decode: (raw: unknown) => T): T[] { return Array.isArray(value) && value.length <= max ? value.map(decode) : invalid() }
function unique<T>(items: T[]): T[] { return new Set(items).size === items.length ? items : invalid() }
function ids(value: unknown, max: number) { return unique(list(value, max, slug)) }
function day(value: unknown): string { const s = text(value, 10, 10); if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) invalid(); const d = new Date(s+'T00:00:00Z'); return Number.isFinite(d.getTime()) && d.toISOString().slice(0,10) === s ? s : invalid() }
function timestamp(value: unknown): string { const s = text(value, 40, 20); if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(s) || !Number.isFinite(Date.parse(s))) invalid(); day(s.slice(0,10)); return s }
export function serviceAmount(value: unknown): string {
  const s = text(value, 13, 1)
  if (!/^(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?$/.test(s) || /^0(?:\.0{1,2})?$/.test(s)) invalid()
  return s
}
export function serviceAmountMinor(value: string): number { const [whole, fraction = ''] = serviceAmount(value).split('.'); return Number(whole)*100+Number(fraction.padEnd(2,'0')) }
export function decodeServicePreferences(raw: unknown): ServicePreferences {
  const r = exact(raw, ['service_opt_in','member_alias','category_ids','property_class_ids','version','updated_at','can_manage'])
  const enabled = bool(r.service_opt_in), categories = ids(r.category_ids,12), classes = unique(list(r.property_class_ids,10,v=>choice(v,PRIVATE_PROPERTY_CLASSES)))
  if (enabled && (!categories.length || !classes.length)) invalid()
  return { service_opt_in: enabled, member_alias: text(r.member_alias,120,enabled?1:0), category_ids: categories, property_class_ids: classes, version:int(r.version), updated_at:r.updated_at === null ? null : timestamp(r.updated_at), can_manage:bool(r.can_manage) }
}
function provider(raw: unknown): ServiceProvider { const r=exact(raw,['user_code','member_alias','category_ids']); const categories=ids(r.category_ids,12);if(!categories.length)invalid();return {user_code:int(r.user_code,1),member_alias:text(r.member_alias,120,1),category_ids:categories} }
function terms(raw: unknown): ProposalTerms {
  const r = exact(raw,['amount','currency','pricing_basis','scope_text','exclusions','estimated_days','valid_until'])
  return {amount:serviceAmount(r.amount),currency:choice(r.currency,['USD','DOP']),pricing_basis:choice(r.pricing_basis,['fixed','estimate']),scope_text:text(r.scope_text,4000,1),exclusions:text(r.exclusions,2000),estimated_days:r.estimated_days === null ? null:int(r.estimated_days,1,365),valid_until:day(r.valid_until)}
}
function proposal(raw: unknown, recipientId: string): ServiceProposal | null {
  if (raw === null) return null
  const r=exact(raw,['id','recipient_id','revision_no','parent_proposal_id','kind','status','version','amount','currency','pricing_basis','scope_text','exclusions','estimated_days','valid_until','is_expired','created_at'])
  if (uuid(r.recipient_id)!==recipientId) invalid()
  const termKeys=['amount','currency','pricing_basis','scope_text','exclusions','estimated_days','valid_until']
  return {...terms(Object.fromEntries(termKeys.map(key=>[key,r[key]]))),id:uuid(r.id),recipient_id:recipientId,revision_no:int(r.revision_no,1,100),parent_proposal_id:nullableId(r.parent_proposal_id),kind:choice(r.kind,['provider_quote','owner_counter']),status:choice(r.status,['submitted','accepted','declined','withdrawn','superseded','closed','awarded']),version:int(r.version,1),is_expired:bool(r.is_expired),created_at:timestamp(r.created_at)}
}
const recipientStates=['pending','accepted','declined','revoked','closed','awarded'] as const
function capabilities<T extends string>(raw: unknown, keys: readonly T[]): Record<T,boolean> {const r=exact(raw,keys);return Object.fromEntries(keys.map(key=>[key,bool(r[key])])) as Record<T,boolean>}
function managedRecipient(raw: unknown): ManagedRecipient {
  const r=exact(raw,['id','user_code','member_alias','status','version','current_proposal','capabilities']), id=uuid(r.id)
  return {id,user_code:int(r.user_code,1),member_alias:text(r.member_alias,120,1),status:choice(r.status,recipientStates),version:int(r.version,1),current_proposal:proposal(r.current_proposal,id),capabilities:capabilities(r.capabilities,['can_revoke','can_counter','can_award','can_withdraw'])}
}
export function decodeServiceRequest(raw: unknown, expected?: 'managed'|'received'): ServiceRequest {
  const r=object(raw), view=choice(r.view,['managed','received'])
  if (expected && view!==expected) invalid()
  const sharedKeys=['id','view','category_id','title','brief','area_label','requester_alias','desired_start_on','created_at','project_id','can_open_project','capabilities']
  exact(r,[...sharedKeys,...(view==='managed'?['version','status','source','updated_at','recipients']:['property_class','recipient','current_proposal'])])
  const projectId=nullableId(r.project_id), canOpen=bool(r.can_open_project)
  if ((projectId!==null)!==canOpen) invalid()
  const shared={id:uuid(r.id),category_id:slug(r.category_id),title:text(r.title,180,1),brief:text(r.brief,4000,1),area_label:text(r.area_label,120),requester_alias:text(r.requester_alias,120,1),desired_start_on:r.desired_start_on===null?null:day(r.desired_start_on),created_at:timestamp(r.created_at),project_id:projectId,can_open_project:canOpen}
  if(view==='managed'){
    const s=exact(r.source,['property_id','listing_id','title','property_class']), propertyId=nullableId(s.property_id),listingId=nullableId(s.listing_id)
    if((propertyId===null)===(listingId===null)) invalid()
    const recipients=list(r.recipients,5,managedRecipient)
    if(!recipients.length)invalid();unique(recipients.map(item=>item.id));unique(recipients.map(item=>item.user_code))
    return {...shared,view,version:int(r.version,1),status:choice(r.status,['open','awarded','cancelled']),source:{property_id:propertyId,listing_id:listingId,title:text(s.title,4096,1),property_class:choice(s.property_class,PRIVATE_PROPERTY_CLASSES)},updated_at:timestamp(r.updated_at),recipients,capabilities:capabilities(r.capabilities,['can_cancel','can_compare','can_award'])}
  }
  const recipient=exact(r.recipient,['id','status','version']),id=uuid(recipient.id),status=choice(recipient.status,recipientStates)
  if(status==='revoked') invalid()
  return {...shared,view,property_class:choice(r.property_class,PRIVATE_PROPERTY_CLASSES),recipient:{id,status,version:int(recipient.version,1)},current_proposal:proposal(r.current_proposal,id),capabilities:capabilities(r.capabilities,['can_accept','can_decline','can_quote','can_withdraw','can_accept_counter','can_decline_counter'])}
}
function award(raw: unknown): ServiceAward {
  const r=exact(raw,['request','project']), request=decodeServiceRequest(r.request,'managed') as ManagedServiceRequest
  const project=r.project===null?null:{id:uuid(object(r.project).id)}
  if((project?.id??null)!==request.project_id || request.status!=='awarded') invalid()
  return {request,project}
}
function bindAward(value:ServiceAward,input:ServiceAction['input']):ServiceAward {
  const recipientId=uuid(input.recipient_id),proposalId=uuid(input.proposal_id)
  const winners=value.request.recipients.filter(recipient=>recipient.status==='awarded')
  const winner=winners.length===1?winners[0]:null,proposal=winner?.current_proposal
  if(!winner||!proposal||winner.id!==recipientId||proposal.id!==proposalId||proposal.status!=='awarded')invalid()
  return value
}
function versions(raw: unknown,audience:ServiceAudience) {
  const keys=audience==='preferences'?['preference_version']:audience==='received'?['recipient_version','proposal_version']:['request_version','recipient_version','proposal_version']
  const r=exact(raw,keys);return Object.fromEntries(Object.entries(r).map(([key,value])=>[key,int(value)]))
}
function current(raw: unknown,operation:ServiceOperation,audience:ServiceAudience,input:ServiceAction['input']):ServiceCurrent {return operation==='preference_save'?decodeServicePreferences(raw):operation==='request_award'?bindAward(award(raw),input):decodeServiceRequest(raw,audience==='received'?'received':'managed')}
function sameRequest(value: ServiceRequest,id:string) {return value.id===uuid(id)?value:invalid()}
const config=(signal?:AbortSignal)=>({signal,withCredentials:true,timeout:15000})
async function page<T>(path:string,params:Record<string,string|number>,decode:(raw:unknown)=>T,key:(item:T)=>string|number,signal?:AbortSignal) {
  int(params.page,1,1000)
  const r=object((await client.get(path,{...config(signal),params})).data),items=list(r.items,30,decode)
  if(int(r.page,1,1000)!==params.page || int(r.page_size)!==30 || int(r.total)<items.length) invalid()
  unique(items.map(key));return {items,total:int(r.total),page:int(r.page),page_size:30} as NetworkPage<T>
}
export async function getServicePreferences(signal?:AbortSignal) {return decodeServicePreferences((await client.get(PREFIX+'/me/service-preferences',config(signal))).data)}
export function getServiceProviders(category_id:string,property_class:string,pageNumber=1,signal?:AbortSignal) {return page(PREFIX+'/service-providers',{category_id:slug(category_id),property_class:choice(property_class,PRIVATE_PROPERTY_CLASSES),page:pageNumber},provider,item=>item.user_code,signal)}
export async function getServiceRequests(view:'managed'|'received',status:string,pageNumber=1,signal?:AbortSignal):Promise<NetworkPage<ServiceRequest>&{can_create:boolean}> {
  choice(view,['managed','received']);choice(status,view==='managed'?['all','open','awarded','cancelled']:['all','actionable','closed','awarded'])
  const params={view,status,page:int(pageNumber,1,1000)},r=object((await client.get(PREFIX+'/service-requests',{...config(signal),params})).data)
  const items=list(r.items,30,item=>decodeServiceRequest(item,view));unique(items.map(item=>item.id))
  if(int(r.page)!==pageNumber||int(r.page_size)!==30||int(r.total)<items.length)invalid()
  return {items,total:int(r.total),page:pageNumber,page_size:30,can_create:bool(r.can_create)}
}
export async function getServiceRequest(id:string,view:'managed'|'received',signal?:AbortSignal) {uuid(id);choice(view,['managed','received']);return sameRequest(decodeServiceRequest((await client.get(PREFIX+'/service-requests/'+id,{...config(signal),params:{view}})).data,view),id)}
export async function getServiceComparison(id:string,signal?:AbortSignal):Promise<ServiceComparison> {
  uuid(id);const r=exact((await client.get(PREFIX+'/service-requests/'+id+'/comparison',config(signal))).data,['request_id','request_version','groups','unquoted'])
  if(uuid(r.request_id)!==id)invalid()
  const groups=list(r.groups,2,raw=>{const g=exact(raw,['currency','items']);const currency=choice(g.currency,['USD','DOP']);const items=list(g.items,5,managedRecipient);if(items.some(item=>!item.current_proposal||item.current_proposal.currency!==currency))invalid();return {currency,items}})
  unique(groups.map(g=>g.currency));const unquoted=list(r.unquoted,5,managedRecipient)
  const combined=[...groups.flatMap(g=>g.items),...unquoted]
  if(combined.length>5||unquoted.some(item=>item.current_proposal!==null))invalid()
  unique(combined.map(item=>item.id));unique(combined.map(item=>item.user_code))
  return {request_id:id,request_version:int(r.request_version,1),groups,unquoted}
}
export function getServiceEvents(id:string,audience:'managed'|'received',pageNumber=1,signal?:AbortSignal,ownRecipientId?:string):Promise<NetworkPage<ServiceEvent>> {
  uuid(id);const own=audience==='received'?uuid(ownRecipientId):null
  return page(PREFIX+'/service-requests/'+id+'/events',{page:pageNumber,view:audience},raw=>{const r=exact(raw,['id','action','occurred_at','recipient_id','proposal_id','result_versions']);const recipientId=nullableId(r.recipient_id),proposalId=nullableId(r.proposal_id);if(own&&(recipientId!==null&&recipientId!==own||recipientId===null&&proposalId!==null))invalid();return {id:uuid(r.id),action:slug(r.action),occurred_at:timestamp(r.occurred_at),recipient_id:recipientId,proposal_id:proposalId,result_versions:versions(r.result_versions,audience)}},item=>item.id,signal)
}
export async function getServiceProposals(requestId:string,recipientId:string,pageNumber=1,signal?:AbortSignal,view?:'managed'|'received'):Promise<NetworkPage<ServiceProposal>> {
  uuid(requestId);uuid(recipientId)
  const result=await page(PREFIX+'/service-requests/'+requestId+'/recipients/'+recipientId+'/proposals',{page:pageNumber,...(view?{view:choice(view,['managed','received'])}:{})},raw=>proposal(raw,uuid(recipientId))??invalid(),item=>item.id,signal)
  if(result.items.some((item,index)=>index>0&&result.items[index-1].revision_no<=item.revision_no))invalid()
  return result
}
export async function getServiceSubmission(action:ServiceAction,signal?:AbortSignal):Promise<ServiceSubmission> {
  const key=uuid(action.input.submission_id),r=exact((await client.get(PREFIX+'/service-actions/submissions/'+key,config(signal))).data,['submission_id','operation','target_id','applied_at','result_versions','current'])
  if(uuid(r.submission_id)!==key || choice(r.operation,SERVICE_OPERATIONS)!==action.operation)invalid()
  const decoded=current(r.current,action.operation,action.audience,action.input)
  const target=uuid(r.target_id)
  if(action.operation==='preference_save'){if(target!==uuid(action.expected_actor_id))invalid()}
  else {
    const request='request'in decoded?decoded.request:'view'in decoded?decoded:invalid()
    if(action.request_id&&request.id!==uuid(action.request_id))invalid()
    if(action.recipient_id){const recipientId=uuid(action.recipient_id);if(request.view==='received'?request.recipient.id!==recipientId:!request.recipients.some(item=>item.id===recipientId))invalid()}
    if(action.operation==='request_create'){
      if(request.view!=='managed'||target!==request.id||request.source.property_id!==nullableId(action.input.property_id)||request.source.listing_id!==nullableId(action.input.listing_id))invalid()
      const codes=unique(list(action.input.recipient_user_codes,5,value=>int(value,1))).sort((a,b)=>a-b)
      if(JSON.stringify(request.recipients.map(item=>item.user_code).sort((a,b)=>a-b))!==JSON.stringify(codes))invalid()
      for(const key of ['title','brief','area_label','requester_alias','category_id'] as const)if(request[key]!==text(action.input[key],4000).trim())invalid()
      if(request.source.property_class!==action.input.property_class||request.desired_start_on!==action.input.desired_start_on)invalid()
    }else if(['recipient_accept','recipient_decline','recipient_revoke','provider_quote','owner_counter'].includes(action.operation)){if(target!==uuid(action.recipient_id))invalid()}
    else if(['counter_accept','counter_decline','proposal_withdraw'].includes(action.operation)){if(target!==uuid(action.proposal_id))invalid()}
    else if(target!==uuid(action.request_id))invalid()
  }
  return {submission_id:key,operation:action.operation,target_id:target,applied_at:timestamp(r.applied_at),result_versions:versions(r.result_versions,action.audience),current:decoded}
}
export function prepareServiceAction(action:ServiceAction):{path:string;method:'post'|'put';body:Record<string,unknown>} {
  try {
  const {operation,audience}=action;choice(operation,SERVICE_OPERATIONS);choice(audience,['preferences','managed','received'])
  const r=object(action.input),submission_id=uuid(r.submission_id)
  let path='',method:'post'|'put'='post',body:Record<string,unknown>={submission_id}
  if(operation==='preference_save'){
    uuid(action.expected_actor_id)
    if(audience!=='preferences')invalid();exact(r,['submission_id','expected_version','service_opt_in','member_alias','category_ids','property_class_ids'])
    const pref=decodeServicePreferences({service_opt_in:r.service_opt_in,member_alias:text(r.member_alias,120).trim(),category_ids:ids(r.category_ids,12).sort(),property_class_ids:unique(list(r.property_class_ids,10,v=>choice(v,PRIVATE_PROPERTY_CLASSES))).sort(),version:int(r.expected_version),updated_at:null,can_manage:true})
    body={submission_id,expected_version:pref.version,service_opt_in:pref.service_opt_in,member_alias:pref.member_alias,category_ids:pref.category_ids,property_class_ids:pref.property_class_ids};path='/me/service-preferences';method='put'
  }else if(operation==='request_create'){
    if(audience!=='managed')invalid();exact(r,['submission_id','property_id','listing_id','category_id','property_class','title','brief','area_label','requester_alias','desired_start_on','recipient_user_codes','shared_brief_acknowledged'])
    const property_id=nullableId(r.property_id),listing_id=nullableId(r.listing_id),codes=unique(list(r.recipient_user_codes,5,v=>int(v,1))).sort((a,b)=>a-b)
    if((property_id===null)===(listing_id===null)||!codes.length||r.shared_brief_acknowledged!==true)invalid()
    body={submission_id,property_id,listing_id,category_id:slug(r.category_id),property_class:choice(r.property_class,PRIVATE_PROPERTY_CLASSES),title:text(text(r.title,180).trim(),180,1),brief:text(text(r.brief,4000).trim(),4000,1),area_label:text(r.area_label,120).trim(),requester_alias:text(text(r.requester_alias,120).trim(),120,1),desired_start_on:r.desired_start_on===null?null:day(r.desired_start_on),recipient_user_codes:codes,shared_brief_acknowledged:true};path='/service-requests'
  }else{
    const requestId=action.request_id===undefined?undefined:uuid(action.request_id),recipientId=action.recipient_id===undefined?undefined:uuid(action.recipient_id),proposalId=action.proposal_id===undefined?undefined:uuid(action.proposal_id)
    const requestPath='/service-requests/'+(requestId??invalid())
    if(['recipient_accept','recipient_decline','recipient_revoke','provider_quote','owner_counter'].includes(operation)){
      path=requestPath+'/recipients/'+(recipientId??invalid())+'/'+({recipient_accept:'accept',recipient_decline:'decline',recipient_revoke:'revoke',provider_quote:'proposals',owner_counter:'counter'}[operation as 'recipient_accept'])
    }else if(['counter_accept','counter_decline','proposal_withdraw'].includes(operation))path='/service-proposals/'+(proposalId??invalid())+'/'+({counter_accept:'accept-counter',counter_decline:'decline-counter',proposal_withdraw:'withdraw'}[operation as 'counter_accept'])
    else path=requestPath+'/'+(operation==='request_award'?'award':'cancel')
    const keys=['submission_id']
    if(['recipient_revoke','owner_counter','request_cancel','request_award'].includes(operation)){keys.push('expected_request_version');body.expected_request_version=int(r.expected_request_version,1)}
    if(operation!=='request_cancel'){keys.push('expected_recipient_version');body.expected_recipient_version=int(r.expected_recipient_version,1)}
    if(['provider_quote','owner_counter'].includes(operation)){keys.push('current_proposal_id','expected_proposal_version','terms');body.current_proposal_id=nullableId(r.current_proposal_id);body.expected_proposal_version=r.expected_proposal_version===null?null:int(r.expected_proposal_version,1);if((body.current_proposal_id===null)!==(body.expected_proposal_version===null))invalid();body.terms=terms(r.terms)}
    else if(['counter_accept','counter_decline','proposal_withdraw','request_award'].includes(operation)){keys.push('expected_proposal_version');body.expected_proposal_version=int(r.expected_proposal_version,1)}
    if(operation==='request_award'){keys.push('recipient_id','proposal_id','project_title','project_summary','project_scope_acknowledged');if(r.project_scope_acknowledged!==true)invalid();body={...body,recipient_id:uuid(r.recipient_id),proposal_id:uuid(r.proposal_id),project_title:text(text(r.project_title,180).trim(),180,1),project_summary:text(r.project_summary,4000),project_scope_acknowledged:true}}
    exact(r,keys)
  }
  return {path,method,body}
  } catch(cause) {
    if(cause instanceof ServiceContractError)throw new ServiceInputError()
    throw cause
  }
}
export async function runServiceAction(action:ServiceAction,signal?:AbortSignal):Promise<ServiceCurrent> {
  const {operation,audience}=action
  const {path,method,body}=prepareServiceAction(action)
  const raw=(await client[method](PREFIX+path,body,config(signal))).data
  const result=current(raw,operation,audience,action.input)
  if(action.request_id && 'view'in result && result.id!==uuid(action.request_id))invalid()
  if(action.request_id && 'request'in result && result.request.id!==uuid(action.request_id))invalid()
  if(action.recipient_id&&'view'in result){const id=uuid(action.recipient_id);if(result.view==='received'?result.recipient.id!==id:!result.recipients.some(item=>item.id===id))invalid()}
  return result
}
