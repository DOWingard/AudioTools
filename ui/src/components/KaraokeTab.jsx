import { useState, useRef } from 'react';
import { useAuth } from '@clerk/clerk-react';
import WaveformPlayer from './WaveformPlayer.jsx';
import { useGatedRun, useStorageGuard } from '../AuthContext.jsx';
import StorageConfirmModal from './StorageConfirmModal.jsx';

const API_BASE = '/api';

export default function KaraokeTab() {
    const requireAuth = useGatedRun();
    const { checkBeforeProcess } = useStorageGuard();
    const { getToken } = useAuth();
    const [file, setFile] = useState(null);
    const [loading, setLoading] = useState(false);
    const [progress, setProgress] = useState({ pct: 0, text: '' });
    const creepRef = useRef(null);
    const [resultBlob, setResultBlob] = useState(null);
    const [resultName, setResultName] = useState('');
    const [error, setError] = useState('');
    const fileRef = useRef(null);
    const [dragover, setDragover] = useState(false);
    const [storageConfirmMsg, setStorageConfirmMsg] = useState(null);

    const handleFile = (f) => {
        setFile(f);
        setResultBlob(null);
        setError('');
    };

    const handleProcess = async () => {
        const msg = await checkBeforeProcess(1);
        if (msg) { setStorageConfirmMsg(msg); return; }
        run();
    };

    const run = async () => {
        if (!file) return;
        window.dispatchEvent(new CustomEvent('waveform:stop-all'));
        setLoading(true);
        setError('');
        setResultBlob(null);
        clearInterval(creepRef.current);
        setProgress({ pct: 5, text: 'Validating audio format…' });

        try {
            const form = new FormData();
            form.append('audio', file);

            const token = await getToken();
            const headers = token ? { Authorization: `Bearer ${token}` } : {};

            setProgress({ pct: 12, text: 'Uploading audio file…' });

            let p = 12;
            creepRef.current = setInterval(() => {
                p = Math.min(p + 0.35, 76);
                const text = p < 22 ? 'Uploading audio file…'
                    : p < 38 ? 'Analyzing audio channels…'
                        : p < 52 ? 'Identifying source layers…'
                            : p < 65 ? 'Separating audio sources…'
                                : 'Reconstructing instrumental mix…';
                setProgress({ pct: p, text });
                if (p >= 76) clearInterval(creepRef.current);
            }, 400);

            const resp = await fetch(`${API_BASE}/karaoke`, { method: 'POST', body: form, headers });
            clearInterval(creepRef.current);
            if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);

            setProgress({ pct: 82, text: 'Receiving output file…' });

            const blob = await resp.blob();
            const name = `${file.name.replace(/\.\w+$/, '')}_karaoke.wav`;
            setResultBlob(blob);
            setResultName(name);
            setProgress({ pct: 100, text: 'Vocal removal complete!' });
            localStorage.setItem('lastProcessedAt', String(Date.now()));
            window.dispatchEvent(new CustomEvent('audioProcessed'));
        } catch (e) {
            clearInterval(creepRef.current);
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    const download = () => {
        if (!resultBlob) return;
        const url = URL.createObjectURL(resultBlob);
        const a = document.createElement('a');
        a.href = url;
        a.download = resultName;
        a.click();
        URL.revokeObjectURL(url);
    };

    return (
        <div className="fade-in">
            <div className="card" style={{ marginBottom: '2rem' }}>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem', fontSize: '1.05rem' }}>
                    Remove vocals from any song using automated source separation. Get a clean instrumental / karaoke track instantly.
                </p>

                <div
                    className={`upload-zone ${dragover ? 'dragover' : ''}`}
                    onClick={() => fileRef.current?.click()}
                    onDragOver={(e) => { e.preventDefault(); setDragover(true); }}
                    onDragLeave={() => setDragover(false)}
                    onDrop={(e) => {
                        e.preventDefault();
                        setDragover(false);
                        if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
                    }}
                >
                    <span className="icon">🎤</span>
                    <span className="label">Drop audio here or click to browse</span>
                    <span className="hint">WAV, FLAC, MP3, AAC</span>
                    {file && <span className="file-name">{file.name}</span>}
                    <input
                        ref={fileRef}
                        type="file"
                        hidden
                        accept=".wav,.flac,.mp3,.aac"
                        onChange={(e) => e.target.files[0] && handleFile(e.target.files[0])}
                    />
                </div>

                <button
                    className="btn btn-primary"
                    style={{ marginTop: '1.5rem', width: '100%', justifyContent: 'center' }}
                    disabled={!file || loading}
                    onClick={requireAuth(handleProcess)}
                >
                    {loading ? '⏳ Processing…' : '🎤 Remove Vocals'}
                </button>

                {storageConfirmMsg && (
                    <StorageConfirmModal
                        message={storageConfirmMsg}
                        onYes={() => { setStorageConfirmMsg(null); run(); }}
                        onNo={() => setStorageConfirmMsg(null)}
                    />
                )}

                {loading && (
                    <div style={{ marginTop: '1rem' }}>
                        <div className="progress-text loading-pulse">{progress.text}</div>
                        <div className="progress-bar-wrapper">
                            <div className="progress-bar-fill" style={{ width: `${progress.pct}%` }} />
                        </div>
                    </div>
                )}

                {error && <p className="status-error" style={{ marginTop: '1rem' }}>❌ {error}</p>}

                {resultBlob && (
                    <div className="fade-in" style={{ marginTop: '1.5rem' }}>
                        <p className="status-success">✅ Vocals removed successfully!</p>
                        <WaveformPlayer
                            label="🎵 Instrumental (Karaoke)"
                            audioBlob={resultBlob}
                            fileName={resultName}
                            color="#e63946"
                            autoPlay={true}
                        />
                        <button className="btn btn-secondary" style={{ marginTop: '1rem', width: '100%' }} onClick={download}>
                            ⬇ Download Instrumental
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
