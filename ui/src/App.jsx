import { useEffect, useState } from 'react';
import { BrowserRouter as Router, Routes, Route, NavLink, Navigate, useSearchParams, useNavigate, Link } from 'react-router-dom';
import { useUser, useAuth } from '@clerk/clerk-react';
import { AuthProvider, useAuthContext } from './AuthContext.jsx';
import SignInModal from './components/SignInModal.jsx';
import UserMenu from './components/UserMenu.jsx';
import SubscriptionModal from './components/SubscriptionModal.jsx';
import LimitModal from './components/LimitModal.jsx';
import SyncTagTab from './components/SyncTagTab.jsx';
import StemSeparatorTab from './components/StemSeparatorTab.jsx';
import AudioCutterTab from './components/AudioCutterTab.jsx';
import AudioJoinerTab from './components/AudioJoinerTab.jsx';
import KaraokeTab from './components/KaraokeTab.jsx';
import FormatConverterTab from './components/FormatConverterTab.jsx';
import AudioAnalyzerTab from './components/AudioAnalyzerTab.jsx';
import MyFilesTab from './components/MyFilesTab.jsx';

// ── CheckoutReturn ─────────────────────────────────────────────────────────
// Rendered at /checkout?session_id=... after Stripe redirects back.
// Calls /auth/billing/sync to verify payment and update the profile in context.
function CheckoutReturn() {
    const [searchParams] = useSearchParams();
    const { getToken, isSignedIn } = useAuth();
    const { setProfile } = useAuthContext();
    const navigate = useNavigate();
    const [status, setStatus] = useState('verifying'); // 'verifying' | 'success' | 'error'
    const [message, setMessage] = useState('');

    const sessionId = searchParams.get('session_id');

    useEffect(() => {
        if (!sessionId) {
            navigate('/synctag', { replace: true });
            return;
        }
        // Wait until Clerk has loaded auth state before attempting the token fetch
        if (!isSignedIn) return;

        let cancelled = false;
        (async () => {
            try {
                const token = await getToken();
                const resp = await fetch('/auth/billing/sync', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        Authorization: `Bearer ${token}`,
                    },
                    body: JSON.stringify({ session_id: sessionId }),
                });
                if (cancelled) return;
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
                    throw new Error(err.detail || `HTTP ${resp.status}`);
                }
                const updatedProfile = await resp.json();
                if (cancelled) return;
                setProfile(updatedProfile);
                setStatus('success');
                setTimeout(() => {
                    if (!cancelled) navigate('/synctag', { replace: true });
                }, 1500);
            } catch (e) {
                if (!cancelled) {
                    setStatus('error');
                    setMessage(e.message);
                }
            }
        })();

        return () => { cancelled = true; };
    }, [sessionId, isSignedIn, getToken, setProfile, navigate]);

    const cardStyle = {
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '60vh',
        gap: '1rem',
        textAlign: 'center',
    };

    if (status === 'success') {
        return (
            <div style={cardStyle}>
                <p style={{ fontSize: '1.5rem', color: '#16a34a' }}>Subscription activated!</p>
            </div>
        );
    }
    if (status === 'error') {
        return (
            <div style={cardStyle}>
                <p style={{ color: '#dc2626' }}>{message}</p>
                <Link to="/synctag">← Back</Link>
            </div>
        );
    }
    return (
        <div style={cardStyle}>
            <p style={{ fontSize: '1.1rem', color: 'var(--text-secondary)' }}>
                Verifying your subscription…
            </p>
        </div>
    );
}

const ROUTES = [
    { id: 'synctag', path: '/synctag', label: 'SyncTag' },
    { id: 'separator', path: '/separator', label: 'Stem Separator' },
    { id: 'vocal-remover', path: '/vocal-remover', label: 'Vocal-Remover' },
    { id: 'analyzer', path: '/analyzer', label: 'Track Analyzer' },
    { id: 'cutter', path: '/cutter', label: 'Cutter' },
    { id: 'joiner', path: '/joiner', label: 'Joiner' },
    { id: 'converter', path: '/converter', label: 'Converter' },
    { id: 'my-files', path: '/my-files', label: 'My Files' },
];

function AppInner() {
    const { isSignedIn } = useUser();
    const { getToken } = useAuth();
    const { profile, setProfile, subModalOpen, setSubModalOpen, limitModalOpen, closeLimitModal } = useAuthContext();

    // Fetch/provision user record on sign-in
    useEffect(() => {
        if (!isSignedIn) {
            setProfile(null);
            return;
        }
        (async () => {
            try {
                const token = await getToken();
                const resp = await fetch('/auth/user/me', {
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (resp.ok) setProfile(await resp.json());
            } catch (e) {
                console.error('Failed to fetch user profile', e);
            }
        })();
    }, [isSignedIn, getToken, setProfile]);

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
                                    onClick={() => window.dispatchEvent(new CustomEvent('waveform:stop-all'))}
                                >
                                    {route.label}
                                </NavLink>
                            ))}
                        </nav>

                        <UserMenu />
                    </div>
                </header>

                {/* ── Page Content ────────────────────────────── */}
                <main className="main-content">
                    <Routes>
                        <Route path="/" element={<Navigate to="/synctag" replace />} />
                        <Route path="/checkout" element={<CheckoutReturn />} />
                        <Route path="/synctag" element={<SyncTagTab />} />
                        <Route path="/separator" element={<StemSeparatorTab />} />
                        <Route path="/vocal-remover" element={<KaraokeTab />} />
                        <Route path="/analyzer" element={<AudioAnalyzerTab />} />
                        <Route path="/cutter" element={<AudioCutterTab />} />
                        <Route path="/joiner" element={<AudioJoinerTab />} />
                        <Route path="/converter" element={<FormatConverterTab />} />
                        <Route path="/my-files" element={<MyFilesTab />} />
                    </Routes>
                </main>
            </div>

            {/* ── Modals ──────────────────────────────────── */}
            <SignInModal />
            <SubModalWrapper />
            <LimitModal open={limitModalOpen} onClose={closeLimitModal} />
        </Router>
    );
}

function SubModalWrapper() {
    const { subModalOpen, setSubModalOpen, subModalPlan, setSubModalPlan } = useAuthContext();
    return (
        <SubscriptionModal
            open={subModalOpen}
            initialPlan={subModalPlan}
            onClose={() => { setSubModalOpen(false); setSubModalPlan(null); }}
        />
    );
}

export default function App() {
    return (
        <AuthProvider>
            <AppInner />
        </AuthProvider>
    );
}
