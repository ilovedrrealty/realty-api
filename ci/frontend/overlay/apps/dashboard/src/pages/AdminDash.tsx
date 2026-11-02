import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import type { UserInfo } from '../lib/auth'
import { AdminHome } from './admin/Home'
import { AdminUsers } from './admin/Users'
import { AdminListings } from './admin/Listings'
import { AdminLeads } from './admin/Leads'
import { AdminBookings } from './admin/Bookings'
import { Analytics } from './admin/Analytics'
import { LeadDelivery } from './admin/LeadDelivery'
import { Communications } from './admin/Communications'
import { AdminLocationReview } from './admin/LocationReview'
import { PropertyManagement } from './shared/PropertyManagement'
import { PropertyNetwork } from './shared/PropertyNetwork'
import { AdminSettings } from './admin/Settings'
import { BulkImportListings } from './admin/BulkImportListings'
import { ResearchTools } from './shared/ResearchTools'
import { WebsiteEditor } from './admin/WebsiteEditor'
import { AdminProfessionals } from './admin/Professionals'
import { ProfessionalProfile } from './shared/ProfessionalProfile'
import { Resources } from './buyer/Resources'
import ProfessionalDirectory from './shared/ProfessionalDirectory'
import { ToolHub } from './shared/ToolHub'
import { ROICalculator } from './buyer/ROICalculator'
import type { Role } from '../App'
import type { ToolWorkspace } from '../data/toolHubs'

export default function AdminDash({ go, view = 'home', user, onUserUpdate }: { go: (v: string, openId?: string, workspace?: Role) => void; view?: string; user: UserInfo; onUserUpdate: (updates: Partial<UserInfo>) => void }) {
  const { t } = useTranslation('admin')
  const { t: tNav } = useTranslation('nav')
  const [searchParams] = useSearchParams()

  const pageKey = ['home', 'users', 'listings', 'leads', 'bookings', 'analytics', 'settings', 'bulk-import', 'research', 'website-editor', 'professionals', 'professional-profile'].includes(view) ? view : 'home'
  const sharedTitle = ['resources', 'research', 'professional-directory', 'buyer-tools', 'owner-tools', 'realtor-tools', 'calculator', 'lead-delivery', 'communications', 'location-review', 'property-management', 'property-network'].includes(view)
  const title = sharedTitle ? tNav(view) : t(`pages.${pageKey}.title`)
  const sub = sharedTitle ? '' : t(`pages.${pageKey}.sub`)
  const goToTool = (target: string, workspace?: ToolWorkspace) => go(target, undefined, workspace)

  function renderView() {
    switch (view) {
      case 'users':    return <AdminUsers />
      case 'listings': return <AdminListings />
      case 'leads':    return <AdminLeads onOpenDelivery={(leadId) => go('lead-delivery', leadId)} />
      case 'lead-delivery': return <LeadDelivery initialLeadId={searchParams.get('openId') ?? ''} onOpenLead={(leadId) => go('leads', leadId)} />
      case 'communications': return <Communications user={user} workspace="Admin" />
      case 'location-review': return <AdminLocationReview />
      case 'property-management': return <PropertyManagement user={user} />
      case 'property-network': return <PropertyNetwork user={user} />
      case 'bookings': return <AdminBookings />
      case 'analytics': return <Analytics />
      case 'settings': return <AdminSettings user={user} onUserUpdate={onUserUpdate} />
      case 'bulk-import': return <BulkImportListings />
      case 'research': return <ResearchTools user={user} />
      case 'resources': return <Resources go={go} />
      case 'buyer-tools': return <ToolHub workspace="Buyer" user={user} go={goToTool} />
      case 'owner-tools': return <ToolHub workspace="Owner" user={user} go={goToTool} />
      case 'realtor-tools':
      case 'tools': return <ToolHub workspace="Realtor" user={user} go={goToTool} />
      case 'calculator': return <ROICalculator />
      case 'website-editor': return <WebsiteEditor userId={user.id} />
      case 'professionals': return <AdminProfessionals />
      case 'professional-profile': return <ProfessionalProfile />
      case 'professional-directory': return <ProfessionalDirectory />
      default:         return <AdminHome go={go} />
    }
  }

  return (
    <div>
      <div className="mb-7">
        <div className="text-[11px] font-bold tracking-[.14em] uppercase text-coral mb-1.5">{t('eyebrow')}</div>
        <h1 className="font-sans text-[22px] sm:text-[28px] font-extrabold text-ink tracking-[-0.02em] mb-1">
          {title}
        </h1>
        {sub ? <p className="text-sm text-ink2">{sub}</p> : null}
      </div>
      {renderView()}
    </div>
  )
}
