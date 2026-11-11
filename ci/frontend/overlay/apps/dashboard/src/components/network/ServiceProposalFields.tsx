import { useTranslation } from 'react-i18next'
import { serviceAmount, type ProposalTerms, type ServiceProposal } from '../../api/serviceWorkflow'
import { NETWORK_FIELD, NetworkDate } from './NetworkShared'
import { todayInDR } from '../../api/propertyManagement'

export type TermsDraft={amount:string;currency:'USD'|'DOP';pricing_basis:'fixed'|'estimate';scope_text:string;exclusions:string;estimated_days:string;valid_until:string}
export function termsDraft(proposal?:ServiceProposal|null,scope=''):TermsDraft {return {amount:proposal?.amount??'',currency:proposal?.currency??'USD',pricing_basis:proposal?.pricing_basis??'fixed',scope_text:proposal?.scope_text??scope,exclusions:proposal?.exclusions??'',estimated_days:proposal?.estimated_days==null?'':String(proposal.estimated_days),valid_until:proposal?.valid_until??todayInDR()}}
export function validTerms(draft:TermsDraft) {try{serviceAmount(draft.amount);return !!draft.scope_text.trim()&&/^\d{4}-\d{2}-\d{2}$/.test(draft.valid_until)&&(draft.estimated_days===''||/^\d+$/.test(draft.estimated_days)&&Number(draft.estimated_days)>=1&&Number(draft.estimated_days)<=365)}catch{return false}}
export function termsInput(draft:TermsDraft):ProposalTerms {return {...draft,scope_text:draft.scope_text.trim(),estimated_days:draft.estimated_days===''?null:Number(draft.estimated_days)}}
export function ServiceProposalFields({draft,onChange}:{draft:TermsDraft;onChange:(value:TermsDraft)=>void}) {
  const {t}=useTranslation('property_network')
  const change=(name:keyof TermsDraft,value:string)=>onChange({...draft,[name]:value})
  return <><p className="text-xs text-dim">{t('service.informational')}</p><div className="grid gap-4 sm:grid-cols-3">
    <label className="block text-sm font-semibold">{t('service.amount')}<input className={NETWORK_FIELD} inputMode="decimal" value={draft.amount} maxLength={13} required onChange={e=>change('amount',e.target.value)}/></label>
    <label className="block text-sm font-semibold">{t('service.currency')}<select className={NETWORK_FIELD} value={draft.currency} onChange={e=>change('currency',e.target.value)}><option value="USD">USD</option><option value="DOP">DOP</option></select></label>
    <label className="block text-sm font-semibold">{t('service.pricing_basis')}<select className={NETWORK_FIELD} value={draft.pricing_basis} onChange={e=>change('pricing_basis',e.target.value)}><option value="fixed">{t('service.fixed')}</option><option value="estimate">{t('service.estimate')}</option></select></label></div>
    <label className="block text-sm font-semibold">{t('service.scope')}<textarea className={NETWORK_FIELD} value={draft.scope_text} rows={4} maxLength={4000} required onChange={e=>change('scope_text',e.target.value)}/></label>
    <label className="block text-sm font-semibold">{t('service.exclusions')}<textarea className={NETWORK_FIELD} value={draft.exclusions} rows={2} maxLength={2000} onChange={e=>change('exclusions',e.target.value)}/></label>
    <div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm font-semibold">{t('service.estimated_days')}<input className={NETWORK_FIELD} type="number" min={1} max={365} value={draft.estimated_days} onChange={e=>change('estimated_days',e.target.value)}/></label>
      <label className="block text-sm font-semibold">{t('service.valid_until')}<input className={NETWORK_FIELD} type="date" value={draft.valid_until} required onChange={e=>change('valid_until',e.target.value)}/></label></div><p className="text-xs text-dim">{t('service.expiry_help')}</p>
  </>
}
export function ServiceProposalCard({proposal}:{proposal:ServiceProposal}) {
  const {t}=useTranslation('property_network')
  return <div className="min-w-0 space-y-2 rounded-xl border border-line bg-white p-4"><p className="text-xs">{t('service.kinds.'+proposal.kind)}</p><p className="font-semibold">{proposal.currency} {proposal.amount} · {t('service.'+proposal.pricing_basis)}</p><p className="text-xs text-dim">{t('service.revision',{number:proposal.revision_no})} · {t('service.proposal_states.'+proposal.status)}{proposal.is_expired?' · '+t('service.expired'):''}</p><p className="break-words whitespace-pre-wrap text-sm">{proposal.scope_text}</p><p className="break-words whitespace-pre-wrap text-sm">{t('service.exclusions')}: {proposal.exclusions||'—'}</p><p className="text-sm">{t('service.estimated_days')}: {proposal.estimated_days??'—'}</p><p className="text-sm">{t('service.valid_until')}: <NetworkDate value={proposal.valid_until}/></p><p className="text-xs text-dim">{t('service.informational')}</p></div>
}
