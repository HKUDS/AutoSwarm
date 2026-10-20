import type { InstalledPackageInfo } from '../types/visual'

interface PackageCardProps {
  pkg: InstalledPackageInfo
  onUninstall?: (packageId: string) => void
  uninstallingId?: string | null
}

export function PackageCard({ pkg, onUninstall, uninstallingId }: PackageCardProps) {
  const isUninstalling = uninstallingId === pkg.package_id

  return (
    <div className="pkg-card">
      <div className="pkg-card-header">
        <img
          src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888' d='M20.54 5.23l-1.39-1.68C18.88 3.21 18.47 3 18 3H6c-.47 0-.88.21-1.16.55L3.46 5.23C3.17 5.57 3 6.02 3 6.5V19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6.5c0-.48-.17-.93-.46-1.27zM12 17.5L6.5 12H10v-2h4v2h3.5L12 17.5zM5.12 5l.81-1h12l.94 1H5.12z'/%3E%3C/svg%3E"
          alt="package"
          className="pkg-card-icon"
        />
        <div className="pkg-card-title-wrap">
          <span className="pkg-card-name">{pkg.name || pkg.package_id}</span>
          <span className="pkg-card-version">v{pkg.version}</span>
        </div>
      </div>

      <div className="pkg-card-stats">
        <span className="pkg-card-stat">
          <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888' d='M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z'/%3E%3C/svg%3E" alt="roles" className="pkg-stat-icon" />
          {pkg.role_ids.length} roles
        </span>
        <span className="pkg-card-stat">
          <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888' d='M19 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.11-.9-2-2-2zm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z'/%3E%3C/svg%3E" alt="templates" className="pkg-stat-icon" />
          {pkg.template_ids.length} templates
        </span>
      </div>

      {pkg.installed_at && (
        <div className="pkg-card-date">
          Installed {new Date(pkg.installed_at).toLocaleDateString()}
        </div>
      )}

      {onUninstall && (
        <div className="pkg-card-actions">
          <button
            className="pkg-btn pkg-btn-danger"
            disabled={isUninstalling}
            onClick={(e) => { e.stopPropagation(); onUninstall(pkg.package_id) }}
          >
            {isUninstalling ? 'Removing...' : 'Uninstall'}
          </button>
        </div>
      )}
    </div>
  )
}
