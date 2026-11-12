import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getServiceReview, isServiceReviewRating, normalizeServiceReviewComment, prepareServiceReviewSubmission,
  resolveServiceReview, SERVICE_REVIEW_COMMENT_LIMIT, SERVICE_REVIEW_STARS, submitServiceReview, type ServiceReview,
} from '../../api/serviceReview'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import {
  NETWORK_FIELD, NETWORK_PRIMARY, NetworkDate, NetworkFreshness, NetworkPanel, networkError, useNetworkDraftGuard,
} from './NetworkShared'
import { ServiceMutationStatus, useServiceAttempt } from './ServiceWorkflowShared'

const transport = {
  prepare: prepareServiceReviewSubmission,
  execute: submitServiceReview,
  resolve: resolveServiceReview,
  canRetry: (_action: unknown, current: Awaited<ReturnType<typeof getServiceReview>>) => current.can_submit,
}

export function ServiceReviewPanel({ requestId, sender, parentFresh }: {
  requestId: string
  sender: 'owner' | 'provider'
  parentFresh: boolean
}) {
  const { t } = useTranslation('property_network')
  const fieldId = useId()
  const [rating, setRating] = useState('')
  const [draft, setDraft] = useState('')
  const [observedReview, setObservedReview] = useState<ServiceReview | null>(null)
  const load = (signal: AbortSignal) => getServiceReview(requestId, signal)
  const read = useNetworkRead(`service-review:${requestId}:${sender}`, load)
  const readError = read.error ? networkError(read.error) : ''

  function showSavedReview(value: ServiceReview) {
    setObservedReview(value)
    setRating('')
    setDraft('')
  }

  const mutation = useServiceAttempt(transport, load, value => {
    showSavedReview(value)
    read.refresh()
  }, current => {
    if (current.review) showSavedReview(current.review)
    read.refresh()
  }, readError === 'error_access')

  const denied = readError === 'error_access' || mutation.denied
  const review = read.data?.review ?? observedReview
  const canSubmit = sender === 'owner' && read.data?.can_submit === true && !review
  const fresh = parentFresh && !!read.data && !read.stale && !read.checking && !read.error
  const locked = mutation.busy || mutation.pending !== null
  const stars = Number(rating)
  const comment = normalizeServiceReviewComment(draft)
  const invalidComment = comment === undefined

  useNetworkDraftGuard(!denied && ((canSubmit && (!!rating || !!draft)) || mutation.pending !== null), !denied && mutation.busy)

  function save() {
    if (!canSubmit || !fresh || locked || !isServiceReviewRating(stars) || comment === undefined) return
    mutation.send({ requestId, payload: { submission_id: crypto.randomUUID(), stars, comment } })
  }

  if (denied) return null

  return <NetworkPanel title={t('review.title')} headingId={`${fieldId}-heading`}>
    <NetworkFreshness read={read} />
    <p className="mb-4 text-sm text-dim">{t('review.privacy')}</p>
    {readError ? <p role="alert" className="text-sm text-red-800">{t(readError)}</p> : null}
    {review ? <div className="min-w-0 space-y-2 rounded-xl border border-line bg-white p-3 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="font-semibold text-ink">{t('review.score', { stars: review.stars })}</p>
        <span className="text-xs text-dim"><NetworkDate value={review.created_at} /></span>
      </div>
      {review.comment ? <p className="whitespace-pre-wrap break-words text-ink2">{review.comment}</p> : null}
    </div> : null}
    {!read.data && !readError && !review ? <p role="status">{t('loading')}</p> : null}
    {read.data && !review && !canSubmit ? <p className="text-sm text-dim">{t('review.empty')}</p> : null}

    {canSubmit ? <form className="space-y-3" onSubmit={event => { event.preventDefault(); save() }}>
      <p className="text-sm text-dim">{t('review.one_review')}</p>
      <fieldset className="min-w-0 space-y-3" disabled={locked}>
        <div>
          <label className="block text-sm font-semibold text-ink" htmlFor={`${fieldId}-rating`}>{t('review.rating')}</label>
          <select id={`${fieldId}-rating`} className={NETWORK_FIELD} required value={rating} onChange={event => setRating(event.target.value)}>
            <option value="">{t('review.choose_rating')}</option>
            {SERVICE_REVIEW_STARS.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-sm font-semibold text-ink" htmlFor={`${fieldId}-comment`}>{t('review.comment')}</label>
          <textarea id={`${fieldId}-comment`} className={NETWORK_FIELD} rows={3} value={draft}
            aria-describedby={`${fieldId}-limit`} aria-invalid={invalidComment} maxLength={SERVICE_REVIEW_COMMENT_LIMIT * 2}
            onChange={event => setDraft(event.target.value)} />
          <p id={`${fieldId}-limit`} className={`text-xs ${invalidComment ? 'text-red-800' : 'text-dim'}`}>
            {t(invalidComment ? 'review.invalid_comment' : 'review.limit', { limit: SERVICE_REVIEW_COMMENT_LIMIT })}
          </p>
        </div>
        <button type="submit" className={NETWORK_PRIMARY} disabled={!fresh || locked || !isServiceReviewRating(stars) || invalidComment}>
          {t('review.save')}
        </button>
      </fieldset>
    </form> : null}
    <ServiceMutationStatus mutation={{ ...mutation,
      checked: mutation.checked && fresh && (mutation.conflicted || canSubmit),
      retry: () => { if (fresh && canSubmit) mutation.retry() },
      reviewConflict: () => { if (fresh) mutation.reviewConflict() },
    }} />
  </NetworkPanel>
}
