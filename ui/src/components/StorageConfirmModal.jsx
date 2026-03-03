import { createPortal } from 'react-dom';

/**
 * Confirmation dialog shown when a standard-tier user's file storage is full
 * or would be partially exceeded by the current operation.
 *
 * Props:
 *   message  — descriptive text (e.g. "Database full, clear out 1 file…")
 *   onYes    — called when user clicks Yes (proceed with processing)
 *   onNo     — called when user clicks No (cancel)
 */
export default function StorageConfirmModal({ message, onYes, onNo }) {
    return createPortal(
        <div
            className="modal-overlay"
            onClick={onNo}
            style={{ zIndex: 10100 }}
        >
            <div
                className="modal-content fade-in"
                style={{ maxWidth: 420, padding: '2rem', textAlign: 'center' }}
                onClick={(e) => e.stopPropagation()}
            >
                <div style={{ fontSize: '2rem', marginBottom: '0.75rem' }}>Storage</div>
                <h3 style={{ margin: '0 0 0.75rem', fontSize: '1.1rem' }}>Storage Limit</h3>
                <p style={{
                    color: 'var(--text-secondary)',
                    fontSize: '0.95rem',
                    lineHeight: 1.55,
                    margin: '0 0 1.5rem',
                }}>
                    {message}
                    <br />
                    <span style={{ fontWeight: 600 }}>Continue?</span>
                </p>
                <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
                    <button
                        className="btn btn-primary"
                        style={{ padding: '0.5rem 1.5rem' }}
                        onClick={onYes}
                    >
                        Yes
                    </button>
                    <button
                        className="btn btn-secondary"
                        style={{ padding: '0.5rem 1.5rem' }}
                        onClick={onNo}
                    >
                        No
                    </button>
                </div>
            </div>
        </div>,
        document.body
    );
}
