import { useTranslation } from 'react-i18next'
import { professionalContactHref, professionalLabel, professionalOptionLabels, professionalServiceLabels, type ProfessionalDraft, type ProfessionalOptions } from '../../../../../packages/professionals/src'
import { ShareContactButton } from './ShareContactDialog'

export function ProfessionalPreview({ profile, options, shareableBusiness = false }: { profile: Omit<ProfessionalDraft, 'public_contact_consent'>; options: ProfessionalOptions; shareableBusiness?: boolean }) {
  const { t, i18n } = useTranslation('professionals')
  const name = profile.business_name || profile.contact_name
  return <article className="min-w-0 rounded-2xl border border-line bg-white p-5 space-y-4 break-words">
    <div className="flex items-start gap-4">
      {profile.photo_url && <img src={profile.photo_url} alt="" className="h-20 w-20 shrink-0 rounded-xl object-cover" />}
      <div className="min-w-0">
        <h3 className="text-xl font-bold text-ink">{name || t('preview_empty')}</h3>
        {profile.contact_name && profile.contact_name !== profile.business_name && <p className="mt-1 text-sm text-ink2">{profile.contact_name}</p>}
        <p className="mt-2 break-words text-xs font-semibold text-coral">{professionalServiceLabels(profile, options.categories, i18n.language).join(' · ')}</p>
      </div>
    </div>
    <p className="text-sm text-ink2">{professionalOptionLabels(profile.service_area_ids, options.areas, i18n.language).join(' · ')}</p>
    <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink2">{professionalLabel(profile.descriptions, i18n.language)}</p>
    {profile.languages.length > 0 && <p className="text-xs text-dim">{t('languages')}: {professionalOptionLabels(profile.languages, options.languages, i18n.language).join(', ')}</p>}
    <div className="flex flex-wrap gap-2">
      {([
        ['phone', profile.public_phone, 'call'], ['whatsapp', profile.public_whatsapp, 'whatsapp'],
        ['email', profile.public_email, 'email'], ['website', profile.website_url, 'website'],
      ] as const).map(([kind, value, key]) => {
        const href = professionalContactHref(kind, value)
        return href ? <a key={key} href={href} target={kind === 'website' || kind === 'whatsapp' ? '_blank' : undefined} rel="noopener noreferrer"
          className="max-w-full rounded-lg border border-line px-3 py-2 text-sm font-semibold text-coral">{t(key)}<span className="block break-all text-xs font-normal text-ink2">{value}</span></a> : null
      })}
    </div>
    {shareableBusiness ? <ShareContactButton contact={{ name, email: profile.public_email, phone: profile.public_phone || profile.public_whatsapp }} privateContact={false} /> : null}
  </article>
}
