import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { Rss, GitCommit, Tag, Activity, Users, Scale } from 'lucide-react'

declare const __APP_VERSION__: string
declare const __GIT_COMMIT__: string

export default function About() {
  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
  })

  return (
    <div className="p-6 max-w-lg space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-gray-100">About</h1>
        <p className="text-sm text-gray-500">ThreatFeeds Lite — version information.</p>
      </div>

      <div className="card space-y-5">
        {/* App identity */}
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-10 h-10 bg-brand-600 rounded-xl">
            <Rss className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="text-base font-semibold text-gray-100">ThreatFeeds Lite</p>
            <p className="text-xs text-gray-500">Lightweight Threat Intelligence feed receiver</p>
          </div>
        </div>

        <div className="border-t border-gray-800" />

        {/* Version details */}
        <dl className="space-y-3">
          <div className="flex items-center gap-3">
            <Tag className="w-4 h-4 text-brand-400 shrink-0" />
            <dt className="text-sm text-gray-400 w-32">Version</dt>
            <dd className="text-sm font-mono text-gray-200">{__APP_VERSION__}</dd>
          </div>

          <div className="flex items-center gap-3">
            <GitCommit className="w-4 h-4 text-brand-400 shrink-0" />
            <dt className="text-sm text-gray-400 w-32">Git Commit</dt>
            <dd className="text-sm font-mono text-gray-200 truncate" title={__GIT_COMMIT__}>
              {__GIT_COMMIT__ === 'dev' ? 'dev (not built from git)' : __GIT_COMMIT__.slice(0, 12)}
            </dd>
          </div>

          <div className="flex items-center gap-3">
            <Activity className="w-4 h-4 text-brand-400 shrink-0" />
            <dt className="text-sm text-gray-400 w-32">Backend</dt>
            <dd className="text-sm">
              {health ? (
                <span className="badge bg-green-900/50 text-green-400 border border-green-800/50">
                  Online · v{health.version}
                </span>
              ) : (
                <span className="badge bg-red-900/50 text-red-400 border border-red-800/50">
                  Offline
                </span>
              )}
            </dd>
          </div>

          <div className="flex items-center gap-3">
            <Users className="w-4 h-4 text-brand-400 shrink-0" />
            <dt className="text-sm text-gray-400 w-32">Code Dev Team</dt>
            <dd className="text-sm text-gray-200">
              Javier Santillan{' '}
              <a
                href="mailto:jusafing@jusanet.org"
                className="text-gray-500 hover:text-gray-300"
              >
                jusafing@jusanet.org
              </a>
            </dd>
          </div>
        </dl>

        <div className="border-t border-gray-800" />

        <p className="text-xs text-gray-600 leading-relaxed">
          ThreatFeeds Lite is a standalone, local Threat Intelligence feed aggregator.
          It listens for, pulls, and normalises threat intel from multiple sources,
          stores data in SQLite, and exposes this web interface for viewing and configuration.
        </p>
      </div>

      <div className="card space-y-3">
        <div className="flex items-center gap-3">
          <Scale className="w-4 h-4 text-brand-400 shrink-0" />
          <h2 className="text-sm font-semibold text-gray-200">License</h2>
        </div>
        <p className="text-xs text-gray-500 leading-relaxed">
          ThreatFeeds Lite is released under the Apache License 2.0. The full
          terms are in the <span className="font-mono text-gray-400">LICENSE</span>{' '}
          file at the project root.
        </p>
        <p className="text-xs text-gray-500 leading-relaxed">
          This product includes third-party open-source software. Each component
          remains under its own license; see{' '}
          <span className="font-mono text-gray-400">THIRD-PARTY-NOTICES.md</span>{' '}
          for the list of bundled dependencies and their licenses.
        </p>
      </div>
    </div>
  )
}
