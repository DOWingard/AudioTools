import { useState, useCallback, useRef } from 'react';
import { useAuth } from '@clerk/clerk-react';
import { loadStripe } from '@stripe/stripe-js';
import { EmbeddedCheckoutProvider, EmbeddedCheckout } from '@stripe/react-stripe-js';

const stripePromise = loadStripe(import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY);

const PLANS = [
    {
        id: 'standard',
        name: 'Standard',
        price: '$9.99 / mo',
        features: ['Unlimited use of all services'],
        color: '#7c3aed',
    },
    {
        id: 'premium',
        name: 'Premium',
        price: '$14.99 / mo',
        features: ['File storage in smart database', 'Smart retrieval'],
        color: '#d97706',
    },
];

export default function SubscriptionModal({ open, onClose, initialPlan = null }) {
    const { getToken } = useAuth();
    const [selectedPlan, setSelectedPlan] = useState(initialPlan);

    // Sync selectedPlan when modal opens or initialPlan changes
    const prevOpen = useRef(false);
    if (open && !prevOpen.current) { selectedPlan !== initialPlan && setSelectedPlan(initialPlan); }
    prevOpen.current = open;
    const [error, setError] = useState('');

    const fetchClientSecret = useCallback(async () => {
        try {
            const token = await getToken();
            const resp = await fetch('/auth/billing/checkout', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${token}`,
                },
                body: JSON.stringify({ plan: selectedPlan }),
            });
            if (!resp.ok) {
                const msg = await resp.text();
                throw new Error(`Checkout failed (${resp.status}): ${msg}`);
            }
            const { client_secret } = await resp.json();
            return client_secret;
        } catch (e) {
            setError(e.message);
            throw e;
        }
    }, [getToken, selectedPlan]);

    const handleClose = () => {
        setSelectedPlan(null);
        setError('');
        onClose();
    };

    if (!open) return null;

    return (
        <div className="modal-overlay" onClick={handleClose}>
            <div
                className="modal-content"
                style={{ maxWidth: selectedPlan ? '720px' : '640px', transition: 'max-width 0.2s' }}
                onClick={(e) => e.stopPropagation()}
            >
                <button className="modal-close" onClick={handleClose} aria-label="Close">✕</button>

                {!selectedPlan ? (
                    /* ── Plan picker ────────────────────────────────── */
                    <>
                        <h2 style={{ marginTop: 0, marginBottom: '1.5rem', textAlign: 'center' }}>
                            Upgrade your plan
                        </h2>

                        <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
                            {PLANS.map((plan) => (
                                <div
                                    key={plan.id}
                                    style={{
                                        flex: '1 1 220px',
                                        border: `2px solid ${plan.color}`,
                                        borderRadius: '12px',
                                        padding: '1.5rem',
                                    }}
                                >
                                    <h3 style={{ color: plan.color, margin: '0 0 0.25rem' }}>{plan.name}</h3>
                                    <p style={{ fontSize: '1.5rem', fontWeight: 700, margin: '0 0 1rem' }}>
                                        {plan.price}
                                    </p>
                                    <ul style={{ paddingLeft: '1.2rem', margin: '0 0 1.5rem', color: 'var(--text-secondary)' }}>
                                        {plan.features.map((f) => <li key={f}>{f}</li>)}
                                    </ul>
                                    <button
                                        className="btn btn-primary"
                                        style={{
                                            width: '100%',
                                            justifyContent: 'center',
                                            background: plan.color,
                                            borderColor: plan.color,
                                        }}
                                        onClick={() => { setError(''); setSelectedPlan(plan.id); }}
                                    >
                                        Choose {plan.name}
                                    </button>
                                </div>
                            ))}
                        </div>

                        {error && <p className="status-error" style={{ marginTop: '1rem' }}>Error: {error}</p>}
                    </>
                ) : (
                    /* ── Embedded Stripe Checkout ───────────────────── */
                    <>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.25rem' }}>
                            <button
                                onClick={() => setSelectedPlan(null)}
                                style={{
                                    background: 'none',
                                    border: 'none',
                                    cursor: 'pointer',
                                    fontSize: '1.1rem',
                                    color: 'var(--text-secondary)',
                                    padding: '2px 6px',
                                }}
                                aria-label="Back to plans"
                            >
                                ← Back
                            </button>
                            <h2 style={{ margin: 0, fontSize: '1.1rem' }}>
                                {PLANS.find(p => p.id === selectedPlan)?.name} — {PLANS.find(p => p.id === selectedPlan)?.price}
                            </h2>
                        </div>

                        {error ? (
                            <p className="status-error" style={{ marginTop: '1rem' }}>Error: {error}</p>
                        ) : (
                            <EmbeddedCheckoutProvider
                                stripe={stripePromise}
                                options={{ fetchClientSecret }}
                            >
                                <EmbeddedCheckout />
                            </EmbeddedCheckoutProvider>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}
