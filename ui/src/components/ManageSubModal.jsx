import { useState, useEffect } from 'react';
import { useAuth } from '@clerk/clerk-react';
import { useAuthContext } from '../AuthContext.jsx';

export default function ManageSubModal({ open, onClose }) {
    const { getToken } = useAuth();
    const { profile, setProfile } = useAuthContext();

    const isPremium = profile?.subscription_type === 'premium';

    const [view,    setView]    = useState('confirm');
    const [action,  setAction]  = useState('cancel');
    const [loading, setLoading] = useState(false);
    const [error,   setError]   = useState('');
    const [done,    setDone]    = useState(false);

    // Reset state cleanly when the modal opens.
    // useEffect is used (not the render body) to avoid triggering
    // extra renders in React 19 Strict Mode.
    useEffect(() => {
        if (open) {
            setView(isPremium ? 'choice' : 'confirm');
            setAction(isPremium ? null : 'cancel');
            setError('');
            setDone(false);
        }
    }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleClose = () => {
        setLoading(false);
        onClose();
    };

    const chooseAction = (chosen) => {
        setAction(chosen);
        setView('confirm');
    };

    const execute = async () => {
        setLoading(true);
        setError('');
        try {
            const token    = await getToken();
            const endpoint = action === 'downgrade'
                ? '/auth/billing/downgrade'
                : '/auth/billing/cancel';
            const resp = await fetch(endpoint, {
                method:  'POST',
                headers: { Authorization: `Bearer ${token}` },
            });
            if (!resp.ok) {
                const text = await resp.text();
                let msg;
                try {
                    const data = JSON.parse(text);
                    msg = data.detail || `Request failed (${resp.status})`;
                } catch {
                    msg = text || `Request failed (${resp.status})`;
                }
                throw new Error(msg);
            }
            const updatedProfile = await resp.json();
            setProfile(updatedProfile);
            setDone(true);
            setTimeout(handleClose, 2200);
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    if (!open) return null;

    const CONFIRM_COPY = {
        cancel: {
            heading:  'Cancel subscription?',
            body:     'Your subscription will end at the end of your current billing period. You will retain access until then, after which your account will revert to the free tier.',
            cta:      'Yes, cancel subscription',
            ctaColor: '#dc2626',
        },
        downgrade: {
            heading:  'Downgrade to Standard?',
            body:     'You will be moved to the Standard plan ($9.99/mo). Premium features (file storage, smart retrieval) will be removed at the end of the current billing period.',
            cta:      'Yes, downgrade to Standard',
            ctaColor: '#7c3aed',
        },
    };

    return (
        <div className="modal-overlay" onClick={handleClose}>
            <div
                className="modal-content"
                style={{ maxWidth: 480 }}
                onClick={(e) => e.stopPropagation()}
            >
                <button className="modal-close" onClick={handleClose} aria-label="Close">✕</button>

                {done ? (
                    <div style={{ textAlign: 'center', padding: '2rem 1rem' }}>
                        <div style={{ fontSize: '2rem', marginBottom: '0.75rem' }}>✓</div>
                        <p style={{ fontWeight: 600, fontSize: '1.1rem', margin: 0 }}>
                            {action === 'downgrade' ? 'Downgrade scheduled.' : 'Cancellation scheduled.'}
                        </p>
                        <p style={{ color: 'var(--text-secondary)', marginTop: '0.5rem', fontSize: '0.9rem' }}>
                            {action === 'downgrade'
                                ? 'Your plan will downgrade to Standard at the end of the billing period.'
                                : 'Your subscription will end at the end of your billing period.'}
                        </p>
                    </div>
                ) : view === 'choice' ? (
                    <>
                        <h2 style={{ marginTop: 0, marginBottom: '0.5rem' }}>Manage subscription</h2>
                        <p style={{ color: 'var(--text-secondary)', marginTop: 0, marginBottom: '1.5rem', fontSize: '0.9rem' }}>
                            You are on the <strong>Premium</strong> plan ($14.99/mo).
                        </p>

                        <div
                            style={{ border: '1px solid #E0E0E0', borderRadius: 8, padding: '1.25rem', marginBottom: '1rem', cursor: 'pointer' }}
                            onClick={() => chooseAction('downgrade')}
                        >
                            <div style={{ fontWeight: 700, marginBottom: '0.25rem' }}>
                                Downgrade to Standard — $9.99/mo
                            </div>
                            <div style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                                Keep unlimited use of all tools. Lose file storage and smart retrieval.
                            </div>
                            <button
                                className="btn btn-primary"
                                style={{ marginTop: '1rem', background: '#7c3aed', borderColor: '#7c3aed' }}
                                onClick={(e) => { e.stopPropagation(); chooseAction('downgrade'); }}
                            >
                                Switch to Standard
                            </button>
                        </div>

                        <div
                            style={{ border: '1px solid #E0E0E0', borderRadius: 8, padding: '1.25rem', cursor: 'pointer' }}
                            onClick={() => chooseAction('cancel')}
                        >
                            <div style={{ fontWeight: 700, marginBottom: '0.25rem' }}>
                                Cancel subscription
                            </div>
                            <div style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                                Revert to the free tier (3 uses/day). All stored files will be removed.
                            </div>
                            <button
                                className="btn btn-primary"
                                style={{ marginTop: '1rem', background: '#dc2626', borderColor: '#dc2626' }}
                                onClick={(e) => { e.stopPropagation(); chooseAction('cancel'); }}
                            >
                                Cancel subscription
                            </button>
                        </div>
                    </>
                ) : (
                    <>
                        {isPremium && (
                            <button
                                onClick={() => setView('choice')}
                                style={{
                                    background: 'none', border: 'none', cursor: 'pointer',
                                    fontSize: '1rem', color: 'var(--text-secondary)',
                                    padding: '0 0 1rem', display: 'block',
                                }}
                            >
                                ← Back
                            </button>
                        )}
                        <h2 style={{ marginTop: 0, marginBottom: '0.75rem' }}>
                            {CONFIRM_COPY[action].heading}
                        </h2>
                        <p style={{ color: 'var(--text-secondary)', marginTop: 0, marginBottom: '1.5rem', fontSize: '0.9rem', lineHeight: 1.6 }}>
                            {CONFIRM_COPY[action].body}
                        </p>

                        {error && (
                            <p style={{ color: '#dc2626', fontSize: '0.875rem', marginBottom: '1rem' }}>
                                {error}
                            </p>
                        )}

                        <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
                            <button
                                className="btn"
                                style={{ border: '1px solid #E0E0E0', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
                                onClick={handleClose}
                                disabled={loading}
                            >
                                Keep my plan
                            </button>
                            <button
                                className="btn btn-primary"
                                style={{ background: CONFIRM_COPY[action].ctaColor, borderColor: CONFIRM_COPY[action].ctaColor }}
                                onClick={execute}
                                disabled={loading}
                            >
                                {loading ? 'Processing…' : CONFIRM_COPY[action].cta}
                            </button>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
