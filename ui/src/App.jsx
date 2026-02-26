import { BrowserRouter as Router, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import SyncTagTab from './components/SyncTagTab.jsx';
import StemSeparatorTab from './components/StemSeparatorTab.jsx';

const ROUTES = [
    { id: 'synctag', path: '/synctag', label: 'SyncTag AI' },
    { id: 'separator', path: '/separator', label: 'Stem Separator' },
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
                    </Routes>
                </main>
            </div>
        </Router>
    );
}
