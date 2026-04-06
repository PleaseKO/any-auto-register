import { useLocation } from 'react-router-dom'
import AppleMailImportPage from '@/pages/mailpool/AppleMailImportPage'
import AppleMailListPage from '@/pages/mailpool/AppleMailListPage'
import OutlookImportPage from '@/pages/mailpool/OutlookImportPage'
import OutlookListPage from '@/pages/mailpool/OutlookListPage'

function useIsImportRoute() {
  const location = useLocation()
  return location.pathname.endsWith('/import')
}

function useIsOutlookRoute() {
  const location = useLocation()
  return location.pathname.startsWith('/mailpool/outlook')
}

export default function EmailPool() {
  const isImportRoute = useIsImportRoute()
  const isOutlookRoute = useIsOutlookRoute()
  if (isOutlookRoute) {
    return isImportRoute ? <OutlookImportPage /> : <OutlookListPage />
  }
  return isImportRoute ? <AppleMailImportPage /> : <AppleMailListPage />
}
