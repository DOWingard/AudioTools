import { useState, useCallback } from 'react';
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

export default function LimitModal({ open, onClose }) {
    const { getToken } = useAuth();
    const [selectedPlan, setSelectedPlan] = useState(null);

    const fetchClientSecret = useCallback(async () => {
        const token = await getToken();
        const resp = await fetch('/auth/billing/checkout', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({ plan: selectedPlan }),
        });
        if (!resp.ok) throw new Error(`Checkout failed (${resp.status})`);
        const { client_secret } = await resp.json();
        return client_secret;
    }, [getToken, selectedPlan]);

    const handleClose = () => {
        setSelectedPlan(null);
        onClose();
    };

    if (!open) return null;

    return (
        <div className="modal-overlay" onClick={handleClose}>
            <div
                className="modal-content"
                style={{ maxWidth: selectedPlan ? '720px' : '560px', transition: 'max-width 0.2s' }}
                onClick={(e) => e.stopPropagation()}
            >
                <button className="modal-close" onClick={handleClose} aria-label="Close">✕</button>

                {!selectedPlan ? (
                    <>
                        <div style={{ textAlign: 'center', marginBottom: '1.5rem' }}>
                            <div style={{ fontSize: '2.5rem', marginBottom: '0.5rem' }}>🚫</div>
                            <h2 style={{ margin: '0 0 0.4rem' }}>Daily limit reached</h2>
                            <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '0.95rem' }}>
                                You've used all 3 free daily uses. Upgrade to continue processing tracks.
                            </p>
                        </div>

                        <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
                            {PLANS.map((plan) => (
                                <div
                                    key={plan.id}
                                    style={{
                                        flex: '1 1 200px',
                                        border: `2px solid ${plan.color}`,
                                        borderRadius: '12px',
                                        padding: '1.25rem',
                                    }}
                                >
                                    <h3 style={{ color: plan.color, margin: '0 0 0.25rem' }}>{plan.name}</h3>
                                    <p style={{ fontSize: '1.4rem', fontWeight: 700, margin: '0 0 0.75rem' }}>
                                        {plan.price}
                                    </p>
                                    <ul style={{ paddingLeft: '1.2rem', margin: '0 0 1.25rem', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
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
                                        onClick={() => setSelectedPlan(plan.id)}
                                    >
                                        Choose {plan.name}
                                    </button>
                                </div>
                            ))}
                        </div>
                    </>
                ) : (
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

                        <EmbeddedCheckoutProvider
                            stripe={stripePromise}
                            options={{ fetchClientSecret }}
                        >
                            <EmbeddedCheckout />
                        </EmbeddedCheckoutProvider>
                    </>
                )}
            </div>
        </div>
    );
}
