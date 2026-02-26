import { BrowserRouter as Router, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import SyncTagTab from './components/SyncTagTab.jsx';
import StemSeparatorTab from './components/StemSeparatorTab.jsx';
import AudioCutterTab from './components/AudioCutterTab.jsx';
import AudioJoinerTab from './components/AudioJoinerTab.jsx';
import KaraokeTab from './components/KaraokeTab.jsx';
import FormatConverterTab from './components/FormatConverterTab.jsx';
import AudioAnalyzerTab from './components/AudioAnalyzerTab.jsx';

const ROUTES = [
    { id: 'synctag', path: '/synctag', label: 'SyncTag' },
    { id: 'separator', path: '/separator', label: 'Stem Separator' },
    { id: 'vocal-remover', path: '/vocal-remover', label: 'Vocal-Remover' },
    { id: 'analyzer', path: '/analyzer', label: 'Track Analyzer' },
    { id: 'cutter', path: '/cutter', label: 'Cutter' },
    { id: 'joiner', path: '/joiner', label: 'Joiner' },
    { id: 'converter', path: '/converter', label: 'Converter' },
];

export default function App() {
    return (
        <Router>
            <div className="app-shell">
                {/* ── Top Bar ─────────────────────────────────── */}
                <header className="top-bar">
                    <div className="top-bar-inner">
                        <div className="top-bar-brand">
                            <span className="brand-icon">♫</span>
                            <span className="brand-text">Audio Pipeline</span>
                        </div>

                        <nav className="top-bar-nav">
                            {ROUTES.map((route) => (
                                <NavLink
                                    key={route.id}
                                    to={route.path}
                                    className={({ isActive }) => `tab-link ${isActive ? 'active' : ''}`}
                                >
                                    {route.label}
                                </NavLink>
                            ))}
                        </nav>

                        <button className="auth-btn" onClick={() => { }}>
                            Sign In / Sign Up
                        </button>
                    </div>
                </header>

                {/* ── Page Content ────────────────────────────── */}
                <main className="main-content">
                    <Routes>
                        <Route path="/" element={<Navigate to="/synctag" replace />} />
                        <Route path="/synctag" element={<SyncTagTab />} />
                        <Route path="/separator" element={<StemSeparatorTab />} />
                        <Route path="/vocal-remover" element={<KaraokeTab />} />
                        <Route path="/analyzer" element={<AudioAnalyzerTab />} />
                        <Route path="/cutter" element={<AudioCutterTab />} />
                        <Route path="/joiner" element={<AudioJoinerTab />} />
                        <Route path="/converter" element={<FormatConverterTab />} />
                    </Routes>
                </main>
            </div>
        </Router>
    );
}
