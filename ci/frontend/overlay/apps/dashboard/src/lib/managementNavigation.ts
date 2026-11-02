/** Navigation only. The destination independently authenticates its own Kaoba owner. */
export function managementNavigation(accountRole: string, workspace: string): string | null {
  return accountRole === 'admin' && workspace === 'Admin' ? 'https://management.kaobahotel.com/' : null
}
