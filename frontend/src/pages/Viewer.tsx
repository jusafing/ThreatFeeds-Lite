import { useState } from 'react'
import SummaryTable from '../components/SummaryTable'
import EntryTable from '../components/EntryTable'
import NormalizedTable from '../components/NormalizedTable'
import { clsx } from 'clsx'

type Tab = 'summary' | 'raw' | 'normalized'

export default function Viewer() {
  const [activeTab, setActiveTab] = useState<Tab>('summary')

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-gray-100">Viewer</h1>
        <p className="text-sm text-gray-500">Overview and live feed of ingested threat intelligence.</p>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-800">
        <nav className="flex gap-6">
          <button
            onClick={() => setActiveTab('summary')}
            className={clsx(
              'pb-3 text-sm font-medium transition-colors',
              activeTab === 'summary' ? 'tab-active' : 'tab-inactive',
            )}
          >
            Summary
          </button>
          <button
            onClick={() => setActiveTab('raw')}
            className={clsx(
              'pb-3 text-sm font-medium transition-colors',
              activeTab === 'raw' ? 'tab-active' : 'tab-inactive',
            )}
          >
            Raw Feeds
          </button>
          <button
            onClick={() => setActiveTab('normalized')}
            className={clsx(
              'pb-3 text-sm font-medium transition-colors',
              activeTab === 'normalized' ? 'tab-active' : 'tab-inactive',
            )}
          >
            Normalized Feeds
          </button>
        </nav>
      </div>

      <div>
        {activeTab === 'summary' && (
          <div className="max-w-3xl">
            <SummaryTable />
          </div>
        )}
        {activeTab === 'raw' && <EntryTable />}
        {activeTab === 'normalized' && <NormalizedTable />}
      </div>
    </div>
  )
}
