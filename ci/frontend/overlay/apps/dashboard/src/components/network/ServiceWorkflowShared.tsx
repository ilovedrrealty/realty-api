import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { isAxiosError } from 'axios'
import { ServiceInputError, getServiceSubmission, prepareServiceAction, runServiceAction, type ServiceAction, type ServiceCurrent } from '../../api/serviceWorkflow'
import { NETWORK_BUTTON, networkError } from './NetworkShared'

/** One actor/context-scoped service attempt; never retries as a side effect. */
export function useServiceMutation(reauthorize: (signal: AbortSignal) => Promise<unknown>, onApplied: (value: ServiceCurrent) => void, onReviewed: (value: unknown) => void, blocked = false) {
  const [pending, setPending] = useState<ServiceAction | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState(''), [checked, setChecked] = useState(false), [denied, setDenied] = useState(false), [saved,setSaved]=useState(false)
  const alive=useRef(true), request=useRef<AbortController|null>(null), reviewed=useRef<unknown>(null), conflict=useRef(false), blockedRef=useRef(blocked), inFlight=useRef(false)
  blockedRef.current=blocked
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;request.current?.abort()}},[])
  useEffect(()=>{if(blocked){request.current?.abort();inFlight.current=false;setBusy(false);setDenied(true)}},[blocked])
  const active=(controller:AbortController)=>alive.current && !blockedRef.current && request.current===controller && !controller.signal.aborted
  function complete(value:ServiceCurrent) {setPending(null);setError('');setChecked(false);setSaved(true);conflict.current=false;reviewed.current=null;onApplied(value)}
  async function execute(action:ServiceAction) {
    if(inFlight.current||denied||blockedRef.current||!alive.current)return
    inFlight.current=true
    const controller=new AbortController();request.current=controller;setPending(action);setBusy(true);setChecked(false);setError('');setSaved(false)
    try {const result=await runServiceAction(action,controller.signal);if(active(controller))complete(result)}
    catch(cause){if(active(controller)){const code=networkError(cause);conflict.current=code==='error_conflict'||code==='error_fields';setError(code);if(code==='error_access')setDenied(true)}}
    finally{if(request.current===controller)inFlight.current=false;if(active(controller))setBusy(false)}
  }
  async function check() {
    if(!pending||inFlight.current||denied||blockedRef.current||!alive.current)return
    inFlight.current=true
    const controller=new AbortController();request.current=controller;setBusy(true);setChecked(false);reviewed.current=null
    try{const result=await getServiceSubmission(pending,controller.signal);if(active(controller))complete(result.current)}
    catch(cause){
      if(!active(controller))return
      if(isAxiosError(cause)&&cause.response?.status===404){
        // A resolver404 is ambiguous. A separate actual authorized read is
        // required even to offer an explicit identical-body/key retry.
        try{const fresh=await reauthorize(controller.signal);if(active(controller)){reviewed.current=fresh;setChecked(true)}}
        catch(access){if(active(controller)){const code=networkError(access);setError(code);if(code==='error_access')setDenied(true)}}
      }else{const code=networkError(cause);setError(code);if(code==='error_access')setDenied(true)}
    }finally{if(request.current===controller)inFlight.current=false;if(active(controller))setBusy(false)}
  }
  function reviewConflict() {
    if(busy||!checked||!conflict.current||reviewed.current===null)return
    onReviewed(reviewed.current);setPending(null);setError('');setChecked(false);conflict.current=false;reviewed.current=null
  }
  return {pending,busy,error,denied,checked,saved,conflicted:conflict.current,
    send:(action:ServiceAction)=>{
      if(pending||inFlight.current||denied||blockedRef.current||!alive.current)return
      // This synchronous, pure boundary cannot have sent anything. A response
      // contract error thrown later by execute must never release its attempt.
      try{prepareServiceAction(action)}
      catch(cause){setError(cause instanceof ServiceInputError?'error_fields':'error_unknown');setSaved(false);return}
      void execute(action)
    },
    check:()=>void check(), retry:()=>{if(pending&&checked&&!conflict.current)void execute(pending)},reviewConflict}
}
export type ServiceMutation = ReturnType<typeof useServiceMutation>
export function ServiceMutationStatus({ mutation }: { mutation:ServiceMutation }) {
  const {t}=useTranslation('property_network')
  const errorRef=useRef<HTMLParagraphElement|null>(null)
  useEffect(()=>{if(mutation.error)errorRef.current?.focus()},[mutation.error])
  return <div className="space-y-3" aria-live="polite">{mutation.error?<p role="alert" tabIndex={-1} ref={errorRef} className="text-sm text-red-800">{t(mutation.error)}</p>:null}{mutation.saved?<p role="status" className="text-sm">{t('service.action_saved')}</p>:null}
    {mutation.pending&&!mutation.denied?<><p className="text-sm text-amber-950">{t(mutation.busy&&!mutation.error?'service.saving_action':'service.uncertain')}</p><button type="button" className={NETWORK_BUTTON} disabled={mutation.busy} onClick={mutation.check}>{t('service.check_result')}</button>
      {mutation.checked?<><p className="text-sm text-dim">{t('service.ambiguous_result')}</p><button type="button" className={NETWORK_BUTTON} disabled={mutation.busy} onClick={mutation.conflicted?mutation.reviewConflict:mutation.retry}>{t(mutation.conflicted?'service.review_conflict':'service.retry_same')}</button></>:null}</>:null}
  </div>
}
export function serviceDraftEqual(left:unknown,right:unknown) {return JSON.stringify(left)===JSON.stringify(right)}
