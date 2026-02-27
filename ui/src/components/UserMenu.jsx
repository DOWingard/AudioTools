import { UserButton, useUser } from '@clerk/clerk-react';
import { useAuthContext } from '../AuthContext.jsx';

const BADGE_COLOR = {
    free: '#6b7280',
    standard: '#7c3aed',
    premium: '#d97706',
};

export default function UserMenu({ profile, onUpgradeClick }) {
    const { isSignedIn } = useUser();
    const { openSignIn } = useAuthContext();

    if (!isSignedIn) {
        return (
            <button className="auth-btn" onClick={openSignIn}>
                Sign In / Sign Up
            </button>
        );
    }

    const subType = profile?.subscription_type || 'free';
    const badgeColor = BADGE_COLOR[subType] || BADGE_COLOR.free;

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
                <button className="auth-btn" onClick={onUpgradeClick}>
                    Upgrade
                </button>
            )}

            <UserButton afterSignOutUrl="/" />
        </div>
    );
}
