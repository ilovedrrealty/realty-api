import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  CONVERSATION_MESSAGE_LIMIT, conversationBody, getServiceConversation, prepareConversationSubmission,
  resolveConversationMessage, sendConversationMessage, type ConversationSender,
} from '../../api/serviceConversation'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import {
  NETWORK_FIELD, NETWORK_PRIMARY, NetworkFreshness, NetworkPagination, NetworkPanel, networkError, useNetworkDraftGuard,
} from './NetworkShared'
import { ServiceMutationStatus, useServiceAttempt } from './ServiceWorkflowShared'

const transport = {
  prepare: prepareConversationSubmission,
  execute: sendConversationMessage,
  resolve: resolveConversationMessage,
}

export function ServiceConversationPanel({ requestId, sender, parentFresh }: {
  requestId: string
  sender: ConversationSender
  parentFresh: boolean
}) {
  const { t, i18n } = useTranslation('property_network')
  const fieldId = useId()
  const helpId = useId()
  const [page, setPage] = useState(1)
  const [draft, setDraft] = useState('')
  const read = useNetworkRead(`service-conversation:${sender}:${requestId}:${page}`, signal => getServiceConversation(requestId, page, signal))
  const readError = read.error ? networkError(read.error) : ''
  const mutation = useServiceAttempt(transport, signal => getServiceConversation(requestId, page, signal), () => {
    setDraft('')
    if (page === 1) read.refresh()
    else setPage(1)
  }, () => read.refresh(), readError === 'error_access')

  const denied = readError === 'error_access' || mutation.denied
  const fresh = parentFresh && !!read.data && !read.stale && !read.checking && !read.error
  const locked = mutation.busy || mutation.pending !== null
  const body = conversationBody(draft)
  const invalidDraft = !!draft.trim() && body === null
  useNetworkDraftGuard(!denied && (!!draft || mutation.pending !== null), !denied && mutation.busy)

  function send() {
    if (!fresh || locked || !body) return
    mutation.send({ requestId, sender, payload: { submission_id: crypto.randomUUID(), body } })
  }

  if (denied) return <NetworkPanel title={t('conversation.heading')}><p role="alert">{t('error_access')}</p></NetworkPanel>

  const dateTime = new Intl.DateTimeFormat(i18n.language.startsWith('es') ? 'es-DO' : 'en-US', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/Santo_Domingo',
  })
  return <NetworkPanel title={t('conversation.heading')}>
    <NetworkFreshness read={read} />
    <p className="mb-4 text-sm text-dim">{t('conversation.privacy')}</p>
    {readError ? <p role="alert" className="text-sm text-red-800">{t(readError)}</p> : null}
    {read.data ? <>
      {read.data.items.length ? <ol className="space-y-3">
        {read.data.items.map(item => <li key={item.id} className="min-w-0 rounded-xl border border-line bg-white p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <p className="text-sm font-semibold text-ink">{t(`conversation.sender.${item.sender}`)}</p>
            <time className="text-xs text-dim" dateTime={item.created_at}>{dateTime.format(new Date(item.created_at))}</time>
          </div>
          <p className="mt-2 whitespace-pre-wrap break-words text-sm text-ink2">{item.body}</p>
        </li>)}
      </ol> : <p className="text-sm text-dim">{t('conversation.empty')}</p>}
      {read.data.total > read.data.page_size || page > 1 ? <NetworkPagination page={page} total={read.data.total}
        busy={read.checking || locked} onChange={next => { setDraft(''); setPage(next) }} /> : null}
    </> : !readError ? <p role="status">{t('loading')}</p> : null}

    {read.data ? <form className="mt-5 space-y-2" onSubmit={event => { event.preventDefault(); send() }}>
      <label className="block text-sm font-semibold text-ink" htmlFor={fieldId}>{t('conversation.message')}</label>
      <textarea id={fieldId} className={NETWORK_FIELD} aria-describedby={helpId} aria-invalid={invalidDraft}
        maxLength={CONVERSATION_MESSAGE_LIMIT * 2} required rows={3} value={draft} disabled={locked}
        onChange={event => setDraft(event.target.value)} />
      <p id={helpId} className={`text-xs ${invalidDraft ? 'text-red-800' : 'text-dim'}`}>
        {t('conversation.limit', { limit: CONVERSATION_MESSAGE_LIMIT })}
      </p>
      <button type="submit" className={NETWORK_PRIMARY} disabled={!fresh || locked || !body}>{t('conversation.send')}</button>
    </form> : null}
    <ServiceMutationStatus mutation={{ ...mutation, checked: mutation.checked && fresh,
      retry: () => { if (fresh) mutation.retry() }, reviewConflict: () => { if (fresh) mutation.reviewConflict() } }} />
  </NetworkPanel>
}
