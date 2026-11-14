import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { professionalLabel, type ProfessionalOptions } from '../../../../../packages/professionals/src'
import {
  CUSTODY_LABEL_MAX, custodyBlocked, custodyMemberCode, getCustodyOptions, prepareCustodyTransfer, resolveCustodyTransfer,
  runCustodyTransfer, validCustodyLabel, type CustodyFields, type CustodyOffer, type CustodyOptions,
} from '../../api/registryCustody'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { allowNetworkNavigation, NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, NetworkFreshness, NetworkPanel,
  networkError, useNetworkDraftGuard } from './NetworkShared'
import { ServiceMutationStatus, useServiceAttempt } from './ServiceWorkflowShared'

type Props = { propertyId: string; propertyVersion: number; parentFresh: boolean; actorCode: number | null
  classes: ProfessionalOptions['property_classes']; onSent: () => void }
const offerTransport = {
  prepare: prepareCustodyTransfer,
  execute: runCustodyTransfer,
  resolve: resolveCustodyTransfer,
  canRetry: (intent: CustodyOffer, current: CustodyOptions) => !custodyBlocked(current)
    && current.property_id === intent.property_id && current.property_version === intent.expected_property_version,
}

export function CustodyPropertyFields({ fields, classes }: { fields: CustodyFields; classes: ProfessionalOptions['property_classes'] }) {
  const { t, i18n } = useTranslation('property_network')
  const propertyClass = classes?.find(option => option.id === fields.property_class)
  const rows = [
    [t('registry.title'), fields.title],
    [t('registry.location'), fields.location],
    [t('registry.address'), fields.address || t('custody.address_empty')],
    [t('property_class'), propertyClass ? professionalLabel(propertyClass.label, i18n.language) : fields.property_class],
    [t('custody.relationship'), t(`registry.relationships.${fields.relationship}`)],
  ]
  return <dl className="grid min-w-0 gap-3 sm:grid-cols-2">
    {rows.map(([label, value]) => <div key={label} className="min-w-0"><dt className="font-semibold">{label}</dt>
      <dd className="whitespace-pre-wrap break-words text-sm">{value}</dd></div>)}
  </dl>
}

export function CustodyTransferControl(props: Props) {
  const { t } = useTranslation('property_network')
  const [open, setOpen] = useState(false)
  return <div className="space-y-3">
    <button type="button" className={NETWORK_BUTTON} aria-expanded={open} disabled={!props.parentFresh}
      onClick={() => { if (!open || allowNetworkNavigation()) setOpen(value => !value) }}>
      {t(open ? 'custody.close' : 'custody.start')}
    </button>
    {open && <CustodyTransferOffer key={props.propertyId} {...props} />}
  </div>
}

function CustodyTransferOffer({ propertyId, propertyVersion, parentFresh, actorCode, classes, onSent }: Props) {
  const { t } = useTranslation('property_network')
  const heading = useId()
  const [member, setMember] = useState(''), [label, setLabel] = useState(''), [reviewed, setReviewed] = useState<number | null>(null)
  const load = (signal: AbortSignal) => getCustodyOptions(propertyId, signal)
  const read = useNetworkRead(`custody-options:${propertyId}`, load, parentFresh)
  const error = read.error ? networkError(read.error) : ''
  const mutation = useServiceAttempt(offerTransport, load, () => { setMember(''); setLabel(''); setReviewed(null) }, () => {
    setReviewed(null)
    read.refresh()
  }, error === 'error_access')
  const denied = error === 'error_access' || mutation.denied
  const options = parentFresh && !read.stale && !read.error && !denied ? read.data : null
  const current = options?.property_version === propertyVersion
  const locked = mutation.busy || mutation.pending !== null
  const code = custodyMemberCode(member)
  const ready = !!options && current && !read.checking && !locked && !custodyBlocked(options)
    && code !== null && code !== actorCode && validCustodyLabel(label) && reviewed === options.property_version
  useNetworkDraftGuard(!!(member || label || reviewed || mutation.pending) && !denied && !mutation.saved, mutation.busy)
  useEffect(() => { if (denied) { setMember(''); setLabel(''); setReviewed(null) } }, [denied])
  useEffect(() => { if (read.data && read.data.property_version !== reviewed && reviewed !== null) setReviewed(null) }, [read.data, reviewed])
  useEffect(() => { if (read.data && read.data.property_version !== propertyVersion) read.refresh() }, [propertyVersion])

  function submit() {
    if (!ready || !options || code === null) return
    mutation.send({ operation: 'offer', submission_id: crypto.randomUUID(), property_id: options.property_id,
      expected_property_version: options.property_version, recipient_user_code: code, label, transfer_acknowledged: true })
  }

  return <NetworkPanel title={t('custody.start')} headingId={heading}>
    {mutation.saved ? <div className="space-y-3">
      <p role="status" className="text-sm">{t('custody.offer_saved')}</p>
      <button type="button" className={NETWORK_BUTTON} onClick={onSent}>{t('custody.open_sent')}</button>
    </div> : <>
      {!denied && <NetworkFreshness read={read} />}
      {error && <p role="alert" className="text-sm text-red-800">{t(error)}</p>}
      {options && !current && <p role="status" className="text-sm">{t('custody.refresh_property')}</p>}
      {options?.pending_transfer_id && <div className="space-y-3">
        <p role="status" className="text-sm">{t('custody.already_pending')}</p>
        <button type="button" className={NETWORK_BUTTON} disabled={locked} onClick={onSent}>{t('custody.open_sent')}</button>
      </div>}
      {options && !!(options.blockers.active_projects || options.blockers.open_requests || options.blockers.unfinished_awards) && <div className="space-y-2">
        <h3 className="font-semibold">{t('custody.blockers')}</h3>
        <ul className="list-disc pl-5 text-sm">
          {Object.entries(options.blockers).filter(([, count]) => count > 0).map(([kind, count]) =>
            <li key={kind}>{t(`custody.${kind}`)} ({count})</li>)}
        </ul>
      </div>}
      {options && current && !locked && !custodyBlocked(options) && <form className="space-y-4" onSubmit={event => { event.preventDefault(); submit() }}>
        <div className="space-y-2 rounded-xl border border-line p-3">
          <h3 className="font-semibold">{t('custody.offer_fields')}</h3>
          <CustodyPropertyFields fields={options.fields} classes={classes} />
        </div>
        <div className="space-y-2 text-sm"><p>{t('custody.offer_warning')}</p><p>{t('custody.exclusions')}</p><p>{t('custody.expiry_rule')}</p></div>
        <fieldset className="space-y-3" disabled={read.checking}>
          <div>
            <label htmlFor={`${heading}-code`} className="block text-sm font-semibold">{t('custody.member_code')}</label>
            <input id={`${heading}-code`} aria-describedby={`${heading}-code-help`} className={NETWORK_FIELD}
              value={member} inputMode="numeric" autoComplete="off" maxLength={10} required
              onChange={event => { setMember(event.target.value); setReviewed(null) }} />
            <p id={`${heading}-code-help`} className="mt-1 text-sm text-dim">{t('custody.member_code_help')}</p>
          </div>
          <div>
            <label htmlFor={`${heading}-label`} className="block text-sm font-semibold">{t('custody.label')}</label>
            <input id={`${heading}-label`} aria-describedby={`${heading}-label-help`} className={NETWORK_FIELD}
              value={label} maxLength={CUSTODY_LABEL_MAX} required onChange={event => { setLabel(event.target.value); setReviewed(null) }} />
            <p id={`${heading}-label-help`} className="mt-1 text-sm text-dim">{t('custody.label_help')}</p>
          </div>
          <label className="flex min-h-11 items-start gap-3 text-sm font-semibold">
            <input type="checkbox" checked={reviewed === options.property_version}
              onChange={event => setReviewed(event.target.checked ? options.property_version : null)} />
            {t('custody.offer_consent')}
          </label>
          <button type="submit" className={NETWORK_PRIMARY} disabled={!ready}>{t('custody.offer_submit')}</button>
        </fieldset>
      </form>}
      <ServiceMutationStatus mutation={mutation} />
    </>}
  </NetworkPanel>
}
