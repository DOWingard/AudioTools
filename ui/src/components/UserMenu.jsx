import { UserButton, useUser } from '@clerk/clerk-react';
import { useAuthContext } from '../AuthContext.jsx';

const BADGE_COLOR = {
    free: '#6b7280',
    standard: '#7c3aed',
    premium: '#d97706',
};

function UsageIcon({ remaining }) {
    const color = remaining === 0 ? '#ef4444' : '#6b7280';
    return (
        <span style={{ fontSize: '0.8rem', fontWeight: 700, color, fontFamily: 'monospace' }}>
            {remaining}/3
        </span>
    );
}

export default function UserMenu() {
    const { isSignedIn } = useUser();
    const { openSignIn, profile, setSubModalOpen } = useAuthContext();

    if (!isSignedIn) {
        return (
            <button className="auth-btn" onClick={openSignIn}>
                Sign In / Sign Up
            </button>
        );
    }

    const subType = profile?.subscription_type || 'free';
    const badgeColor = BADGE_COLOR[subType] || BADGE_COLOR.free;
    const remaining = profile?.daily_free_downloads ?? 3;

    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <span
                style={{
                    fontSize: '0.7rem',
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    padding: '2px 8px',
                    borderRadius: '9999px',
                    background: badgeColor,
                    color: '#fff',
                }}
            >
                {subType}
            </span>

            {subType === 'free' && (
                <button className="auth-btn" onClick={() => setSubModalOpen(true)}>
                    Upgrade
                </button>
            )}

            <UserButton afterSignOutUrl="/">
                <UserButton.MenuItems>
                    {subType === 'free' ? (
                        <UserButton.Action
                            label="daily uses"
                            labelIcon={<UsageIcon remaining={remaining} />}
                            onClick={() => remaining === 0 && setSubModalOpen(true)}
                        />
                    ) : (
                        <UserButton.Action
                            label="Unlimited uses"
                            labelIcon={
                                <span style={{ fontSize: '1.5rem', lineHeight: 1, fontWeight: 700, color: '#6b7280', fontFamily: 'monospace' }}>
                                    ∞
                                </span>
                            }
                            onClick={() => { }}
                        />
                    )}
                </UserButton.MenuItems>
            </UserButton>
        </div>
    );
}
