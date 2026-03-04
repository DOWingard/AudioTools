import { SignIn } from '@clerk/clerk-react';
import { useAuthContext } from '../AuthContext.jsx';

export default function SignInModal() {
    const { signInOpen, closeSignIn } = useAuthContext();

    if (!signInOpen) return null;

    return (
        <div className="modal-overlay" onClick={closeSignIn}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <button className="modal-close" onClick={closeSignIn} aria-label="Close">✕</button>
                <SignIn
                    routing="hash"
                    fallbackRedirectUrl="/"
                    appearance={{
                        elements: {
                            rootBox: { width: '100%' },
                            card: { boxShadow: 'none', border: 'none', background: 'transparent' },
                        },
                    }}
                />
            </div>
        </div>
    );
}
