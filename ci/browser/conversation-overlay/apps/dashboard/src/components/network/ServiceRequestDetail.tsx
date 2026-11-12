import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getProject } from '../../api/propertyNetwork'
import { SERVICE_OPERATIONS, getServiceComparison, getServiceEvents, getServiceProposals, getServiceRequest, type ManagedRecipient, type ServiceAction, type ServiceOperation, type ServiceRequest, type ServiceProposal } from '../../api/serviceWorkflow'
import { professionalLabel, type ProfessionalOptions } from '../../../../../packages/professionals/src'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, NetworkDate, NetworkFreshness, NetworkPagination, NetworkPanel, allowNetworkNavigation, networkError, useNetworkDraftGuard } from './NetworkShared'
import { ServiceMutationStatus, useServiceMutation } from './ServiceWorkflowShared'
import { ServiceProposalCard, ServiceProposalFields, termsDraft, termsInput, validTerms, type TermsDraft } from './ServiceProposalFields'
import { ServiceWorkPanel } from './ServiceWorkPanel'
import { ServiceConversationPanel } from './ServiceConversationPanel'

type Editor={operation:'provider_quote'|'owner_counter'|'request_award';recipientId:string;requestVersion:number|null;recipientVersion:number;proposalId:string|null;proposalVersion:number|null;terms:TermsDraft;projectTitle:string;projectSummary:string;ack:boolean}
function thread(row:ServiceRequest,id:string) {
  if(row.view==='received')return row.recipient.id===id?{id,version:row.recipient.version,proposal:row.current_proposal}:null
  const recipient=row.recipients.find(item=>item.id===id)
  return recipient?{id,version:recipient.version,proposal:recipient.current_proposal}:null
}
function reviewedEditor(editor:Editor,row:ServiceRequest):Editor {
  const current=thread(row,editor.recipientId)
  return current?{...editor,requestVersion:row.view==='managed'?row.version:null,recipientVersion:current.version,proposalId:current.proposal?.id??null,proposalVersion:current.proposal?.version??null,ack:false}:editor
}
export function ServiceRequestDetail({id,view,options,onBack,onProject}:{id:string;view:'managed'|'received';options:ProfessionalOptions;onBack:()=>void;onProject:(id:string)=>void}) {
  const {t,i18n}=useTranslation('property_network')
  const read=useNetworkRead('service-detail:'+view+':'+id,signal=>getServiceRequest(id,view,signal))
  const [editor,setEditor]=useState<Editor|null>(null),[historyRecipient,setHistoryRecipient]=useState(''),[historyPage,setHistoryPage]=useState(1),[eventPage,setEventPage]=useState(1)
  const [reviewed,setReviewed]=useState<{row:ServiceRequest;previous:ServiceRequest|null}|null>(null)
  const row=reviewed?.row??read.data
  useEffect(()=>{if(reviewed&&read.data!==reviewed.previous&&!read.stale&&!read.error)setReviewed(null)},[reviewed,read.data,read.stale,read.error])
  const comparison=useNetworkRead('service-comparison:'+id,signal=>getServiceComparison(id,signal),row?.view==='managed'&&row.capabilities.can_compare)
  const events=useNetworkRead('service-events:'+view+':'+id+':'+eventPage,signal=>getServiceEvents(id,view,eventPage,signal,row?.view==='received'?row.recipient.id:undefined),!!row)
  const ownRecipient=row?.view==='received'?row.recipient.id:historyRecipient
  const history=useNetworkRead('service-proposal-history:'+view+':'+id+':'+ownRecipient+':'+historyPage,signal=>getServiceProposals(id,ownRecipient,historyPage,signal,view),!!row&&!!ownRecipient)
  const readDenied=[read.error,comparison.error,events.error,history.error].some(error=>!!error&&networkError(error)==='error_access')
  const mutation=useServiceMutation(signal=>getServiceRequest(id,view,signal),()=>{setEditor(null);read.refresh();comparison.refresh();events.refresh();history.refresh()},value=>{
    const fresh=value as ServiceRequest
    setReviewed({row:fresh,previous:read.data})
    if(editor)setEditor(reviewedEditor(editor,fresh))
    read.refresh();comparison.refresh();events.refresh();history.refresh()
  },readDenied)
  const denied=mutation.denied||readDenied
  useNetworkDraftGuard(editor!==null||mutation.pending!==null,mutation.busy)
  const fresh=!!row&&!read.stale&&!read.error&&!read.checking&&!denied
  const locked=mutation.busy||mutation.pending!==null
  function begin(operation:Editor['operation'],recipientId:string) {
    if(!row||!fresh||locked||editor)return
    const current=thread(row,recipientId);if(!current)return
    setEditor({operation,recipientId,requestVersion:row.view==='managed'?row.version:null,recipientVersion:current.version,proposalId:current.proposal?.id??null,proposalVersion:current.proposal?.version??null,terms:termsDraft(current.proposal,row.brief),projectTitle:row.title,projectSummary:current.proposal?.scope_text??row.brief,ack:false})
  }
  function action(operation:ServiceOperation,recipientId?:string,proposal?:ServiceProposal|null) {
    if(!row||!fresh||locked||editor)return
    const current=recipientId?thread(row,recipientId):null
    const input:ServiceAction['input']={submission_id:crypto.randomUUID()}
    if(['recipient_revoke','request_cancel'].includes(operation)){if(row.view!=='managed')return;input.expected_request_version=row.version}
    if(current)input.expected_recipient_version=current.version
    if(proposal)input.expected_proposal_version=proposal.version
    if(['recipient_decline','recipient_revoke','request_cancel','proposal_withdraw'].includes(operation)&&!window.confirm(t('service.confirm_action')))return
    mutation.send({operation,audience:view,request_id:id,...(recipientId?{recipient_id:recipientId}:{}),...(proposal?{proposal_id:proposal.id}:{}),input})
  }
  const selectedThread=row&&editor?thread(row,editor.recipientId):null
  const editorVersionCurrent=!!row&&!!editor&&!!selectedThread&&selectedThread.version===editor.recipientVersion&&(selectedThread.proposal?.id??null)===editor.proposalId&&(selectedThread.proposal?.version??null)===editor.proposalVersion&&(row.view!=='managed'||row.version===editor.requestVersion)
  const managedSelected=row?.view==='managed'&&editor?row.recipients.find(item=>item.id===editor.recipientId):null
  const editorCapability=!!row&&!!editor&&(editor.operation==='provider_quote'?row.view==='received'&&row.capabilities.can_quote:editor.operation==='owner_counter'?!!managedSelected?.capabilities.can_counter:!!managedSelected?.capabilities.can_award&&row.view==='managed'&&row.capabilities.can_award)
  function submitEditor() {
    if(!row||!editor||!fresh||locked||!editorVersionCurrent||!editorCapability)return
    const input:ServiceAction['input']={submission_id:crypto.randomUUID(),expected_recipient_version:editor.recipientVersion}
    if(editor.requestVersion!==null&&editor.operation!=='provider_quote')input.expected_request_version=editor.requestVersion
    if(editor.operation==='request_award'){
      if(!editor.ack||!editor.projectTitle.trim()||!editor.proposalId||!editor.proposalVersion)return
      Object.assign(input,{recipient_id:editor.recipientId,proposal_id:editor.proposalId,expected_proposal_version:editor.proposalVersion,project_title:editor.projectTitle.trim(),project_summary:editor.projectSummary,project_scope_acknowledged:true})
    }else{
      if(!validTerms(editor.terms))return
      Object.assign(input,{current_proposal_id:editor.proposalId,expected_proposal_version:editor.proposalVersion,terms:termsInput(editor.terms)})
    }
    mutation.send({operation:editor.operation,audience:view,request_id:id,recipient_id:editor.recipientId,input})
  }
  function matchComparison(recipient:ManagedRecipient){if(row?.view!=='managed')return false;const current=row.recipients.find(item=>item.id===recipient.id);return !!current&&current.version===recipient.version&&current.current_proposal?.id===recipient.current_proposal?.id&&current.current_proposal?.version===recipient.current_proposal?.version&&comparison.data?.request_version===row.version&&!comparison.stale&&!comparison.error&&!comparison.checking}
  const category=options.categories.find(item=>item.id===row?.category_id)
  const workProjectId=row?.can_open_project&&(row.view==='managed'?row.status==='awarded':row.recipient.status==='awarded')?row.project_id:null
  // Opening a project does not confer access to the owner's private service record.
  const projectAccess=useNetworkRead(`service-work-access:${view}:${id}:${workProjectId}`,signal=>getProject(workProjectId!,signal),!!workProjectId&&!denied&&!mutation.conflicted)
  const projectRole=projectAccess.data?.my_role
  const canReadWork=workProjectId&&(view==='managed'?projectRole==='owner'||projectRole==='admin':projectRole==='professional')
  const conversationSender=projectRole==='owner'&&view==='managed'?'owner'
    :projectRole==='professional'&&view==='received'?'provider':null
  const workAccessFresh=!projectAccess.stale&&!projectAccess.error&&!projectAccess.checking
  return <div className="min-w-0 break-words space-y-5"><button className={NETWORK_BUTTON} onClick={()=>{if(allowNetworkNavigation())onBack()}}>{t('service.back')}</button><NetworkFreshness read={{...read,refresh:()=>{read.refresh();comparison.refresh();events.refresh();history.refresh()}}}/>
    {denied?<p role="alert">{t('error_access')}</p>:<>{read.error?<p role="alert">{t(networkError(read.error))}</p>:null}{row?<>
      {!mutation.conflicted?<><NetworkPanel title={row.title}><p className="text-sm font-semibold">{row.view==='managed'?t('service.request_states.'+row.status):t('service.recipient_states.'+row.recipient.status)}</p><p className="mt-2 text-sm">{category?professionalLabel(category.label,i18n.language):row.category_id}</p><p className="mt-3 break-words whitespace-pre-wrap">{row.brief}</p><dl className="mt-4 grid gap-2 text-sm"><div><dt className="font-semibold">{t('service.requester_alias')}</dt><dd>{row.requester_alias}</dd></div><div><dt className="font-semibold">{t('service.coarse_area')}</dt><dd>{row.area_label||'—'}</dd></div><div><dt className="font-semibold">{t('service.desired_start')}</dt><dd>{row.desired_start_on?<NetworkDate value={row.desired_start_on}/>: '—'}</dd></div></dl><p className="mt-2 text-xs text-dim">{t('service.not_appointment')}</p>{row.view==='managed'?<p className="mt-4 text-sm">{t('service.source')}: {row.source.title}</p>:null}
        {row.can_open_project&&row.project_id?<button className={NETWORK_PRIMARY+' mt-4'} disabled={locked||editor!==null} onClick={()=>{if(row.project_id&&allowNetworkNavigation())onProject(row.project_id)}}>{t('service.open_project')}</button>:null}
      </NetworkPanel>
      {canReadWork?<ServiceWorkPanel key={`${id}:${workProjectId}:${view}`} requestId={id} projectId={workProjectId} parentFresh={fresh&&workAccessFresh&&!locked&&!editor} onProject={onProject}/>:null}
      {workProjectId&&projectAccess.data?.status!=='archived'&&conversationSender?<ServiceConversationPanel key={`${id}:${workProjectId}:${conversationSender}`} requestId={id} sender={conversationSender} parentFresh={fresh&&workAccessFresh&&!locked&&!editor}/>:null}
      {row.view==='received'?<NetworkPanel title={t('service.my_thread')}><p className="mb-3 text-sm">{t('service.accept_help')}</p><div className="flex flex-wrap gap-3">
        {row.capabilities.can_accept?<button className={NETWORK_PRIMARY} disabled={!fresh||locked||editor!==null} onClick={()=>action('recipient_accept',row.recipient.id)}>{t('service.accept')}</button>:null}
        {row.capabilities.can_decline?<button className={NETWORK_BUTTON} disabled={!fresh||locked||editor!==null} onClick={()=>action('recipient_decline',row.recipient.id)}>{t('service.decline')}</button>:null}
        {row.capabilities.can_quote?<button className={NETWORK_PRIMARY} disabled={!fresh||locked||editor!==null} onClick={()=>begin('provider_quote',row.recipient.id)}>{t('service.quote')}</button>:null}
        {row.capabilities.can_withdraw&&row.current_proposal?<button className={NETWORK_BUTTON} disabled={!fresh||locked||editor!==null} onClick={()=>action('proposal_withdraw',row.recipient.id,row.current_proposal)}>{t('service.withdraw')}</button>:null}
        {row.capabilities.can_accept_counter&&row.current_proposal?<button className={NETWORK_PRIMARY} disabled={!fresh||locked||editor!==null} onClick={()=>action('counter_accept',row.recipient.id,row.current_proposal)}>{t('service.accept_counter')}</button>:null}
        {row.capabilities.can_decline_counter&&row.current_proposal?<button className={NETWORK_BUTTON} disabled={!fresh||locked||editor!==null} onClick={()=>action('counter_decline',row.recipient.id,row.current_proposal)}>{t('service.decline_counter')}</button>:null}
      </div>{row.current_proposal?<div className="mt-4"><ServiceProposalCard proposal={row.current_proposal}/></div>:<p className="mt-3 text-sm text-dim">{t('service.no_quote')}</p>}</NetworkPanel>:<>
        <NetworkPanel title={t('service.recipients')}><div className="grid gap-4 sm:grid-cols-2">{row.recipients.map(recipient=><article className="space-y-3 rounded-xl border border-line bg-white p-4" key={recipient.id}><h3 className="font-semibold">{recipient.member_alias}</h3><p className="text-sm">{t('service.recipient_states.'+recipient.status)}</p><button className={NETWORK_BUTTON} disabled={locked||editor!==null} onClick={()=>{setHistoryRecipient(recipient.id);setHistoryPage(1)}}>{t('service.quote_history')}</button>{recipient.capabilities.can_revoke?<button className={NETWORK_BUTTON} disabled={!fresh||locked||editor!==null} onClick={()=>action('recipient_revoke',recipient.id)}>{t('service.revoke')}</button>:null}</article>)}</div></NetworkPanel>
        {row.capabilities.can_compare?<NetworkPanel title={t('service.compare')}><p className="mb-3 text-sm">{t('service.currency_help')}</p><NetworkFreshness read={comparison}/>{comparison.error?<p role="alert">{t(networkError(comparison.error))}</p>:null}{comparison.data?.groups.map(group=><section className="mb-5" key={group.currency}><h3 className="mb-3 font-bold">{group.currency}</h3><div className="grid gap-4 lg:grid-cols-2">{group.items.map(recipient=><article className="min-w-0 space-y-3" key={recipient.id}><h4 className="font-semibold">{recipient.member_alias}</h4>{recipient.current_proposal?<ServiceProposalCard proposal={recipient.current_proposal}/>:null}<div className="flex flex-wrap gap-2">
          {recipient.capabilities.can_counter?<button className={NETWORK_BUTTON} disabled={!fresh||locked||editor!==null||!matchComparison(recipient)} onClick={()=>begin('owner_counter',recipient.id)}>{t('service.counter')}</button>:null}
          {recipient.capabilities.can_award?<button className={NETWORK_PRIMARY} disabled={!fresh||locked||editor!==null||!matchComparison(recipient)} onClick={()=>begin('request_award',recipient.id)}>{t('service.review_award')}</button>:null}
          {recipient.capabilities.can_withdraw&&recipient.current_proposal?<button className={NETWORK_BUTTON} disabled={!fresh||locked||editor!==null||!matchComparison(recipient)} onClick={()=>action('proposal_withdraw',recipient.id,recipient.current_proposal)}>{t('service.withdraw_counter')}</button>:null}
        </div></article>)}</div></section>)}{comparison.data?.unquoted.map(recipient=><p className="mb-2 text-sm" key={recipient.id}>{recipient.member_alias} · {t('service.no_quote')}</p>)}</NetworkPanel>:null}
        {row.capabilities.can_cancel?<button className={NETWORK_BUTTON} disabled={!fresh||locked||editor!==null} onClick={()=>action('request_cancel')}>{t('service.cancel_request')}</button>:null}
      </>}</>:null}
      {editor?<NetworkPanel title={t(editor.operation==='request_award'?'service.review_award':editor.operation==='owner_counter'?'service.counter':'service.quote')}><form className="space-y-4" onSubmit={event=>{event.preventDefault();submitEditor()}}><fieldset className="min-w-0 space-y-4" disabled={locked}>
        {editor.operation==='request_award'?<><p className="text-sm">{t('service.award_help')}</p>{selectedThread?.proposal&&!mutation.conflicted?<ServiceProposalCard proposal={selectedThread.proposal}/>:null}<label className="block text-sm font-semibold">{t('service.project_title')}<input className={NETWORK_FIELD} required value={editor.projectTitle} maxLength={180} onChange={e=>setEditor({...editor,projectTitle:e.target.value,ack:false})}/></label><label className="block text-sm font-semibold">{t('service.project_scope')}<textarea className={NETWORK_FIELD} rows={5} maxLength={4000} value={editor.projectSummary} onChange={e=>setEditor({...editor,projectSummary:e.target.value,ack:false})}/></label><p className="text-sm text-amber-950">{t('service.project_audience')}</p><label className="flex min-h-11 items-start gap-3 text-sm"><input className="mt-1" type="checkbox" required checked={editor.ack} onChange={e=>setEditor({...editor,ack:e.target.checked})}/>{t('service.project_ack')}</label></>:<ServiceProposalFields draft={editor.terms} onChange={terms=>setEditor({...editor,terms})}/>}
        {!editorVersionCurrent?<><p role="status" className="text-sm text-amber-950">{t('service.changed_review')}</p><button type="button" className={NETWORK_BUTTON} disabled={!fresh} onClick={()=>{if(row)setEditor(reviewedEditor(editor,row))}}>{t('service.review_current')}</button></>:null}
        <button className={NETWORK_PRIMARY} disabled={!fresh||!editorCapability||!editorVersionCurrent||(editor.operation==='request_award'?!editor.ack||!editor.projectTitle.trim():!validTerms(editor.terms))}>{t(editor.operation==='request_award'?'service.award':editor.operation==='owner_counter'?'service.submit_counter':'service.submit_quote')}</button>
        <button type="button" className={NETWORK_BUTTON} onClick={()=>{if(allowNetworkNavigation())setEditor(null)}}>{t('service.close_draft')}</button>
      </fieldset></form></NetworkPanel>:null}
      <ServiceMutationStatus mutation={mutation}/>
      {ownRecipient&&!mutation.conflicted?<NetworkPanel title={t('service.quote_history')}><NetworkFreshness read={history}/>{history.error?<p role="alert">{t(networkError(history.error))}</p>:null}<div className="space-y-3">{history.data?.items.map(proposal=><ServiceProposalCard proposal={proposal} key={proposal.id}/>)}</div>{history.data&&!history.data.items.length?<p>{t('service.no_quote')}</p>:null}{history.data?<NetworkPagination page={historyPage} total={history.data.total} busy={history.checking} onChange={setHistoryPage}/>:null}</NetworkPanel>:null}
      {!mutation.conflicted?<NetworkPanel title={t('service.history')}><NetworkFreshness read={events}/>{events.error?<p role="alert">{t(networkError(events.error))}</p>:null}<ol className="space-y-2">{events.data?.items.map(event=><li className="text-sm" key={event.id}><NetworkDate value={event.occurred_at}/> · {t(SERVICE_OPERATIONS.includes(event.action as ServiceOperation)||event.action==='request_closed'?'service.events.'+event.action:'service.recorded_change')}</li>)}</ol>{events.data?<NetworkPagination page={eventPage} total={events.data.total} busy={events.checking} onChange={setEventPage}/>:null}</NetworkPanel>:null}
    </>:!read.error?<p role="status">{t('loading')}</p>:null}</>}
  </div>
}
