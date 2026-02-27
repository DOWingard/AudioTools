import { createContext, useContext, useState, useCallback } from 'react';
import { useAuth } from '@clerk/clerk-react';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [signInOpen, setSignInOpen] = useState(false);
    const [limitModalOpen, setLimitModalOpen] = useState(false);
    const [subModalOpen, setSubModalOpen] = useState(false);
    const [profile, setProfile] = useState(null);

    const openSignIn = useCallback(() => setSignInOpen(true), []);
    const closeSignIn = useCallback(() => setSignInOpen(false), []);
    const openLimitModal = useCallback(() => setLimitModalOpen(true), []);
    const closeLimitModal = useCallback(() => setLimitModalOpen(false), []);

    return (
        <AuthContext.Provider value={{
            signInOpen, openSignIn, closeSignIn,
            limitModalOpen, openLimitModal, closeLimitModal,
            subModalOpen, setSubModalOpen,
            profile, setProfile,
        }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuthContext() {
    return useContext(AuthContext);
}

/**
 * Returns a wrapper: if signed out → opens sign-in modal.
 * If signed in but quota exhausted → opens limit modal.
 * Otherwise consumes one free use and runs fn.
 */
export function useGatedRun() {
    const { isSignedIn, getToken } = useAuth();
    const { openSignIn, openLimitModal, profile, setProfile } = useAuthContext();

    return function gatedRun(fn) {
        return async (...args) => {
            if (!isSignedIn) {
                openSignIn();
                return;
            }

            // Subscribed users skip quota check
            if (profile?.subscription_active) {
                return fn(...args);
            }

            try {
                const token = await getToken();
                const resp = await fetch('/auth/usage/consume', {
                    method: 'POST',
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (!resp.ok) throw new Error(`Usage check failed: ${resp.status}`);
                const { allowed, remaining } = await resp.json();

                if (!allowed) {
                    openLimitModal();
                    return;
                }

                // Update displayed count immediately
                if (remaining !== null && profile) {
                    setProfile((prev) => ({ ...prev, daily_free_downloads: remaining }));
                }
            } catch (e) {
                console.error('Usage consume error', e);
                // Fail open — let the user proceed if the check itself errored
            }

            return fn(...args);
        };
    };
}

/** Legacy alias kept for backward compat — just gates on sign-in, no quota */
export function useRequireAuth() {
    const { isSignedIn } = useAuth();
    const { openSignIn } = useAuthContext();

    return function requireAuth(fn) {
        return (...args) => {
            if (!isSignedIn) {
                openSignIn();
                return;
            }
            return fn(...args);
        };
    };
}
