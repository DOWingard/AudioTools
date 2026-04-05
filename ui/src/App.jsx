import React from 'react';
import './index.css';

function App() {
    return (
        <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: '100vh',
            backgroundColor: '#0a0a0d',
            color: '#ffffff',
            fontFamily: "'Inter', 'Roboto', 'Outfit', sans-serif",
            overflow: 'hidden',
            position: 'relative'
        }}>
            {/* Dynamic Background */}
            <div style={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
                width: '400px',
                height: '400px',
                background: 'radial-gradient(circle, rgba(230, 80, 80, 0.15) 0%, rgba(0,0,0,0) 70%)',
                filter: 'blur(50px)',
                zIndex: 0,
                pointerEvents: 'none',
                animation: 'pulseBg 6s ease-in-out infinite alternate'
            }} />

            {/* Glassmorphism Card */}
            <div style={{
                zIndex: 1,
                padding: '4rem 5rem',
                background: 'rgba(25, 25, 28, 0.65)',
                backdropFilter: 'blur(20px)',
                WebkitBackdropFilter: 'blur(20px)',
                borderRadius: '32px',
                border: '1px solid rgba(255, 255, 255, 0.04)',
                boxShadow: '0 30px 60px -15px rgba(0, 0, 0, 0.6)',
                textAlign: 'center',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)'
            }}>
                {/* Glow Element */}
                <div style={{
                    width: '60px',
                    height: '4px',
                    background: 'linear-gradient(90deg, transparent, rgba(255, 80, 80, 0.8), transparent)',
                    marginBottom: '2rem',
                    borderRadius: '4px'
                }} />

                <h1 style={{
                    fontSize: '3.5rem',
                    fontWeight: '800',
                    letterSpacing: '-0.04em',
                    background: 'linear-gradient(135deg, #ffffff 0%, #a0a5b0 100%)',
                    WebkitBackgroundClip: 'text',
                    WebkitTextFillColor: 'transparent',
                    margin: 0,
                    lineHeight: '1.2'
                }}>
                    Audiotility
                </h1>

                <p style={{
                    marginTop: '1.5rem',
                    fontSize: '1.25rem',
                    color: '#ef4444',
                    letterSpacing: '0.2em',
                    textTransform: 'uppercase',
                    fontWeight: '600',
                    animation: 'fadeInOut 4s infinite'
                }}>
                    Decomissioned
                </p>
            </div>

            <style>
                {`
          @keyframes pulseBg {
            0% { transform: translate(-50%, -50%) scale(1); opacity: 0.6; }
            100% { transform: translate(-50%, -50%) scale(1.3); opacity: 1; }
          }
          @keyframes fadeInOut {
            0%, 100% { opacity: 0.7; }
            50% { opacity: 1; }
          }
          body {
            margin: 0;
            padding: 0;
            background-color: #0a0a0d;
          }
        `}
            </style>
        </div>
    );
}

export default App;
