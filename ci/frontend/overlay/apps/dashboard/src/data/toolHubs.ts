export type ToolWorkspace = 'Buyer' | 'Owner' | 'Realtor'
export type ToolCard = { id: string; icon: 'search' | 'heart' | 'calculator' | 'users' | 'book' | 'home' | 'calendar' | 'leads' | 'research' | 'message'; view?: string; path?: string }

export const TOOL_WORKSPACE_VIEW: Record<ToolWorkspace, string> = {
  Buyer: 'buyer-tools', Owner: 'owner-tools', Realtor: 'realtor-tools',
}

/** Workspace links never grant a role or change the account used by the API. */
export function canOpenToolWorkspace(accountRole: string, workspace: ToolWorkspace): boolean {
  return accountRole === 'admin' || workspace === 'Buyer'
    || (workspace === 'Owner' && accountRole === 'owner')
    || (workspace === 'Realtor' && accountRole === 'realtor')
}

const SHARED: ToolCard[] = [
  { id: 'calculator', icon: 'calculator', view: 'calculator' },
  { id: 'professionals', icon: 'users', view: 'professional-directory' },
  { id: 'network', icon: 'users', view: 'property-network' },
  { id: 'resources', icon: 'book', view: 'resources' },
]

export function workspaceToolCards(workspace: ToolWorkspace, accountRole: string): ToolCard[] {
  if (!canOpenToolWorkspace(accountRole, workspace)) return []
  if (workspace === 'Buyer') return [
    { id: 'buy', icon: 'search', path: '/search/?purpose=sale' },
    { id: 'rent', icon: 'home', path: '/search/?purpose=rent' },
    { id: 'saved', icon: 'heart', view: 'saved' },
    ...SHARED,
    { id: 'buying', icon: 'book', path: '/buying/' },
    { id: 'contact', icon: 'message', path: '/get-started/?intent=buy' },
  ]
  if (workspace === 'Owner') return [
    { id: 'owner_listings', icon: 'home', view: 'listings' },
    { id: 'management', icon: 'home', view: 'property-management' },
    { id: 'calendar', icon: 'calendar', view: 'calendar' },
    { id: 'owner_leads', icon: 'leads', view: 'leads' },
    ...SHARED,
    { id: 'selling', icon: 'book', path: '/selling/' },
    { id: 'contact', icon: 'message', path: '/get-started/?intent=sell' },
  ]
  return [
    { id: 'research', icon: 'research', view: 'research' },
    { id: 'map', icon: 'search', path: '/search/' },
    { id: 'realtor_listings', icon: 'home', view: 'listings' },
    { id: 'management', icon: 'home', view: 'property-management' },
    { id: 'realtor_leads', icon: 'leads', view: 'leads' },
    { id: 'pipeline', icon: 'leads', view: 'pipeline' },
    ...SHARED,
  ]
}
