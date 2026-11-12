import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { getProfessionalOptions } from '../../api/professionals'
import { emptyProfessionalOptions, type ProfessionalOptions } from '../../../../../packages/professionals/src'
import type { UserInfo } from '../../lib/auth'
import { MemberDirectory, MemberProfile } from '../../components/network/MemberCommunity'
import { ProjectDetail, ProjectList } from '../../components/network/ProjectWorkspace'
import { PrivatePropertyDetail, PrivatePropertyList } from '../../components/network/PropertyRegistry'
import { ServiceWorkflow } from '../../components/network/ServiceWorkflow'
import { NETWORK_BUTTON, NETWORK_PRIMARY, networkError } from '../../components/network/NetworkShared'

export function PropertyNetwork({ user }: { user: UserInfo }) {
  const { t } = useTranslation('property_network')
  const [params, setParams] = useSearchParams()
  const tab = ['properties', 'projects', 'services', 'people', 'my-profile'].includes(params.get('tab') ?? '') ? params.get('tab')! : 'projects'
  const projectId = params.get('project') ?? ''
  const propertyId = params.get('property') ?? ''
  const rawMember = params.get('member') ?? ''
  const memberCode = /^[1-9]\d{0,8}$/.test(rawMember) ? Number(rawMember) : undefined
  const [options, setOptions] = useState<ProfessionalOptions>(emptyProfessionalOptions), [catalogError, setCatalogError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    getProfessionalOptions(controller.signal).then(value => { if (!controller.signal.aborted) setOptions(value) }).catch(cause => { if (!controller.signal.aborted) setCatalogError(networkError(cause)) })
    return () => controller.abort()
  }, [])
  function navigate(nextTab: string, key?: 'project' | 'member' | 'property', value?: string, guard = true) {
    if (guard && !window.dispatchEvent(new Event('ildr:before-dashboard-navigation', { cancelable: true }))) return
    const next = new URLSearchParams(params)
    next.set('tab', nextTab); next.delete('project'); next.delete('member'); next.delete('property')
    if (key && value) next.set(key, value)
    setParams(next)
  }
  return <div className="min-w-0 space-y-6"><header><p className="text-xs font-semibold uppercase tracking-wide text-sea">{t('eyebrow')}</p><h1 className="mt-2 text-2xl font-bold text-ink">{t('heading')}</h1><p className="mt-3 max-w-4xl text-sm leading-relaxed text-ink2">{t('intro')}</p></header>
    <nav aria-label={t('main_tabs')} className="flex flex-wrap gap-2">{(['properties', 'projects', 'services', 'people', 'my-profile'] as const).map(value => <button className={tab === value ? NETWORK_PRIMARY : NETWORK_BUTTON} key={value} aria-current={tab === value ? 'page' : undefined} onClick={() => navigate(value)}>{t(`main.${value}`)}</button>)}</nav>
    {catalogError && (tab === 'projects' || tab === 'properties' || tab === 'services') ? <p role="alert" className="text-sm text-red-800">{t(catalogError)}</p> : null}
    {tab === 'services' ? <ServiceWorkflow key={`service:${user.id}`} options={options} role={user.role} actorId={user.id} actorCode={user.user_code} onProject={id => navigate('projects', 'project', id, false)} /> : null}
    {tab === 'properties' ? propertyId ? <PrivatePropertyDetail key={`${user.id}:${propertyId}`} id={propertyId} options={options} actorRole={user.role} onBack={() => navigate('properties', undefined, undefined, false)} onCreateProject={id => navigate('projects', 'property', id, false)} /> : <PrivatePropertyList options={options} onOpen={id => navigate('properties', 'property', id, false)} /> : null}
    {tab === 'projects' ? projectId ? <ProjectDetail key={`${user.id}:${projectId}`} id={projectId} onBack={() => navigate('projects')} /> : <ProjectList key={propertyId || 'all'} options={options} initialPropertyId={propertyId || undefined} canCreate={['admin', 'owner', 'realtor'].includes(user.role) && !catalogError} onOpen={id => navigate('projects', 'project', id)} /> : null}
    {tab === 'people' ? memberCode ? <MemberProfile key={memberCode} code={memberCode} onBack={() => navigate('people')} /> : <MemberDirectory onOpen={code => navigate('people', 'member', String(code))} /> : null}
    {tab === 'my-profile' ? <MemberProfile key={`me:${user.id}`} own /> : null}
  </div>
}
