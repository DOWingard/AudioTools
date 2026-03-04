import { SignIn } from '@clerk/clerk-react';
import { useAuthContext } from '../AuthContext.jsx';

export default function SignInModal() {
    const { signInOpen, signInReturnTo, closeSignIn } = useAuthContext();

    if (!signInOpen) return null;

    return (
        <div className="modal-overlay" onClick={closeSignIn}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <button className="modal-close" onClick={closeSignIn} aria-label="Close">✕</button>
                <SignIn
                    routing="virtual"
                    fallbackRedirectUrl={signInReturnTo || '/'}
                    appearance={{
                        variables: {
                            colorBackground: '#1f1f1f',
                            colorText: '#ebebeb',
                            colorTextSecondary: '#888888',
                            colorInputText: '#ebebeb',
                            colorInputBackground: '#2e2e2e',
                            colorPrimary: '#D50C2D',
                            colorNeutral: '#ebebeb',
                        },
                        elements: {
                            rootBox: { width: '100%' },
                            card: { boxShadow: 'none', border: 'none', background: 'transparent' },
                            input: { border: '1px solid #2e2e2e' },
                        },
                    }}
                />
            </div>
        </div>
    );
}
