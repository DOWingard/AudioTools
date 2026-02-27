import { createContext, useContext, useState, useCallback } from 'react';
import { useAuth } from '@clerk/clerk-react';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [signInOpen, setSignInOpen] = useState(false);

    const openSignIn = useCallback(() => setSignInOpen(true), []);
    const closeSignIn = useCallback(() => setSignInOpen(false), []);

    return (
        <AuthContext.Provider value={{ signInOpen, openSignIn, closeSignIn }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuthContext() {
    return useContext(AuthContext);
}

/**
 * Returns a wrapper function: if the user is signed in, runs `fn`; otherwise
 * opens the sign-in modal.
 */
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
