import { createContext, useContext, useState, useCallback } from 'react';
import { useAuth } from '@clerk/clerk-react';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [signInOpen, setSignInOpen] = useState(false);
    const [limitModalOpen, setLimitModalOpen] = useState(false);
    const [subModalOpen, setSubModalOpen] = useState(false);
    const [subModalPlan, setSubModalPlan] = useState(null);
    const [profile, setProfile] = useState(null);
    // Cached file count for standard-tier quota pre-checks (null = not yet fetched)
    const [fileCount, setFileCount] = useState(null);

    const openSignIn = useCallback(() => setSignInOpen(true), []);
    const closeSignIn = useCallback(() => setSignInOpen(false), []);
    const openLimitModal = useCallback(() => setLimitModalOpen(true), []);
    const closeLimitModal = useCallback(() => setLimitModalOpen(false), []);

    const openSubModal = useCallback((planId = null) => {
        setSubModalPlan(planId);
        setSubModalOpen(true);
    }, []);

    return (
        <AuthContext.Provider value={{
            signInOpen, openSignIn, closeSignIn,
            limitModalOpen, openLimitModal, closeLimitModal,
            subModalOpen, setSubModalOpen, subModalPlan, setSubModalPlan, openSubModal,
            profile, setProfile,
            fileCount, setFileCount,
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

const STANDARD_FILE_LIMIT = 50;

/**
 * Hook for processing tabs to check storage quota before starting a job.
 * Returns checkBeforeProcess(outputFileCount) — resolves to:
 *   null          → no dialog needed, proceed freely
 *   string        → confirmation message; caller must show dialog
 */
export function useStorageGuard() {
    const { profile, fileCount, setFileCount } = useAuthContext();
    const { isSignedIn, getToken } = useAuth();

    const isStandardUser = !!(
        isSignedIn &&
        profile?.subscription_active &&
        profile?.subscription_type !== 'premium'
    );

    const fetchCount = useCallback(async () => {
        try {
            const token = await getToken();
            const resp = await fetch('/api/files/count', {
                headers: { Authorization: `Bearer ${token}` },
            });
            if (!resp.ok) return null;
            const { count } = await resp.json();
            setFileCount(count);
            return count;
        } catch {
            return null; // fail open — backend enforces quota regardless
        }
    }, [getToken, setFileCount]);

    const checkBeforeProcess = useCallback(async (outputFileCount = 1) => {
        if (!isStandardUser) return null;

        const count = fileCount !== null ? fileCount : await fetchCount();
        if (count === null) return null; // fail open

        if (count >= STANDARD_FILE_LIMIT) {
            // Database full
            const n = outputFileCount;
            return `Database full, clear out ${n} file${n !== 1 ? 's' : ''} to save the results.`;
        }

        if (count + outputFileCount > STANDARD_FILE_LIMIT) {
            // Partial save — only applies when outputFileCount > 1 (stem separator)
            const willSave = STANDARD_FILE_LIMIT - count;
            const clearOut = outputFileCount - willSave;
            return `Only ${willSave}/${outputFileCount} files will save, clear out ${clearOut} file${clearOut !== 1 ? 's' : ''} to save the whole result.`;
        }

        return null; // under quota, no confirmation needed
    }, [isStandardUser, fileCount, fetchCount]);

    return { checkBeforeProcess };
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
