import { useEffect, useState, Component } from 'react';
import { BrowserRouter as Router, Routes, Route, NavLink, Navigate, useSearchParams, useNavigate, Link, useLocation } from 'react-router-dom';
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

// ── Route Error Boundary ────────────────────────────────────────────────────
// Catches render errors inside individual route components so a crash on one
// page doesn't unmount the entire React tree and break navigation.
class RouteErrorBoundary extends Component {
    constructor(props) {
        super(props);
        this.state = { error: null };
    }

    static getDerivedStateFromError(error) {
        return { error };
    }

    render() {
        if (this.state.error) {
            return (
                <div style={{ padding: '2.5rem 2rem' }}>
                    <div style={{
                        background: '#fff0f0',
                        border: '1px solid #fca5a5',
                        borderRadius: '0.75rem',
                        padding: '1.5rem 2rem',
                        maxWidth: 680,
                        margin: '0 auto',
                    }}>
                        <h2 style={{ margin: '0 0 0.5rem', color: '#dc2626', fontSize: '1.1rem' }}>
                            ⚠ Page failed to render
                        </h2>
                        <pre style={{
                            color: '#7f1d1d',
                            fontSize: '0.8rem',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                            margin: '0.75rem 0 1rem',
                            background: '#fee2e2',
                            padding: '0.75rem',
                            borderRadius: '0.4rem',
                        }}>
                            {this.state.error?.stack || this.state.error?.message || String(this.state.error)}
                        </pre>
                        <p style={{ margin: 0, color: '#64748b', fontSize: '0.9rem' }}>
                            Navigate to another page to continue. Copy the error above and report it.
                        </p>
                    </div>
                </div>
            );
        }
        return this.props.children;
    }
}

// Resets the error boundary whenever the route path changes so errors on one
// page don't bleed into other pages.
function RouteErrorBoundaryWrapper({ children }) {
    const location = useLocation();
    return (
        <RouteErrorBoundary key={location.pathname}>
            {children}
        </RouteErrorBoundary>
    );
}

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

// ── AppLayout — rendered inside Router so it can use useLocation ────────────
function AppLayout() {
    const location = useLocation();
    const { limitModalOpen, closeLimitModal, isProcessing } = useAuthContext();
    const [menuOpen, setMenuOpen] = useState(false);

    // Close mobile menu whenever the route changes
    useEffect(() => {
        setMenuOpen(false);
    }, [location.pathname]);

    return (
        <>
            <div className={`app-shell${isProcessing ? ' processing-active' : ''}`}>
                {/* ── Top Bar ─────────────────────────────────── */}
                <header className="top-bar">
                    <div className="top-bar-inner">
                        <div className="top-bar-brand">
                            <span className="brand-icon">♫</span>
                            <span className="brand-text">Audio Pipeline</span>
                        </div>

                        {/* Desktop nav — hidden on mobile via CSS */}
                        <nav className="top-bar-nav" aria-label="Main navigation">
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

                        <div className="top-bar-right">
                            <UserMenu />
                            {/* Hamburger — visible on mobile only */}
                            <button
                                className={`hamburger-btn${menuOpen ? ' open' : ''}`}
                                onClick={() => setMenuOpen((v) => !v)}
                                aria-label={menuOpen ? 'Close menu' : 'Open menu'}
                                aria-expanded={menuOpen}
                                aria-controls="mobile-nav"
                            >
                                <span />
                                <span />
                                <span />
                            </button>
                        </div>
                    </div>
                </header>

                {/* Mobile drawer — conditionally rendered when open */}
                {menuOpen && (
                    <nav id="mobile-nav" className="mobile-menu" aria-label="Mobile navigation">
                        {ROUTES.map((route) => (
                            <NavLink
                                key={route.id}
                                to={route.path}
                                className={({ isActive }) => `mobile-menu-link${isActive ? ' active' : ''}`}
                                onClick={() => {
                                    setMenuOpen(false);
                                    window.dispatchEvent(new CustomEvent('waveform:stop-all'));
                                }}
                            >
                                {route.label}
                            </NavLink>
                        ))}
                    </nav>
                )}

                {/* ── Page Content ────────────────────────────── */}
                <main className="main-content">
                    <RouteErrorBoundaryWrapper>
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
                    </RouteErrorBoundaryWrapper>
                </main>
            </div>

            {/* ── Modals ──────────────────────────────────── */}
            <SignInModal />
            <SubModalWrapper />
            <LimitModal open={limitModalOpen} onClose={closeLimitModal} />
        </>
    );
}

function AppInner() {
    const { isSignedIn } = useUser();
    const { getToken } = useAuth();
    const { setProfile } = useAuthContext();

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
            <AppLayout />
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
