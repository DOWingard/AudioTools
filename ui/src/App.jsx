import { useState } from 'react';
import SyncTagTab from './components/SyncTagTab.jsx';
import StemSeparatorTab from './components/StemSeparatorTab.jsx';

const TABS = [
    { id: 'synctag', label: '🏷️ SyncTag AI' },
    { id: 'separator', label: '🎚️ Stem Separator' },
];

export default function App() {
    const [activeTab, setActiveTab] = useState('synctag');

    return (
        <div className="app-shell">
            <header className="app-header">
                <h1>🎛️ Audio Pipeline</h1>
                <p className="subtitle">SyncTag AI &nbsp;•&nbsp; Stem Separator</p>
            </header>

            <nav className="tab-bar">
                {TABS.map((t) => (
                    <button
                        key={t.id}
                        className={`tab-btn ${activeTab === t.id ? 'active' : ''}`}
                        onClick={() => setActiveTab(t.id)}
                    >
                        {t.label}
                    </button>
                ))}
            </nav>

            <main>
                <div style={{ display: activeTab === 'synctag' ? 'block' : 'none' }}>
                    <SyncTagTab />
                </div>
                <div style={{ display: activeTab === 'separator' ? 'block' : 'none' }}>
                    <StemSeparatorTab />
                </div>
            </main>
        </div>
    );
}
