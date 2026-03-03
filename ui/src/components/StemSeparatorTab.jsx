import { useState, useRef } from 'react';
import JSZip from 'jszip';
import { useAuth } from '@clerk/clerk-react';
import WaveformPlayer from './WaveformPlayer.jsx';
import { useGatedRun, useStorageGuard } from '../AuthContext.jsx';
import StorageConfirmModal from './StorageConfirmModal.jsx';

const API_BASE = '/api';

// Stem layout — mirrors the Python _STEM_LAYOUT
const STEM_LAYOUT = [
    { key: 'vocals', label: '🎤 Vocals', group: 'main' },
    { key: 'drums', label: '🥁 Drums', group: 'main' },
    { key: 'sub', label: '🔊 Sub Bass', group: 'main' },
    { key: 'midbass', label: '🎸 Mid/Other', group: 'main' },
    { key: 'drums_kick', label: '👟 Kick', group: 'drums' },
    { key: 'drums_snare', label: '🪘 Snare', group: 'drums' },
    { key: 'drums_toms', label: '🔔 Toms', group: 'drums' },
    { key: 'drums_hihat', label: '🎩 Hi-Hat', group: 'drums' },
    { key: 'drums_cymbals', label: '💿 Cymbals', group: 'drums' },
    { key: 'oneshot_kick', label: '👟 Kick Shot', group: 'oneshots' },
    { key: 'oneshot_snare', label: '🪘 Snare Shot', group: 'oneshots' },
    { key: 'oneshot_toms', label: '🔔 Toms Shot', group: 'oneshots' },
    { key: 'oneshot_hihat', label: '🎩 Hi-Hat Shot', group: 'oneshots' },
    { key: 'oneshot_cymbals', label: '💿 Cymbals Shot', group: 'oneshots' },
];

const GROUP_META = {
    main: { title: '🎵 Main Stems', color: '#7c3aed' },
    drums: { title: '🥁 Drum Stems', color: '#2563eb' },
    oneshots: { title: '🔊 Drum One-Shots', color: '#059669' },
};

export default function StemSeparatorTab() {
    const requireAuth = useGatedRun();
    const { checkBeforeProcess } = useStorageGuard();
    const { getToken } = useAuth();
    const [file, setFile] = useState(null);
    const [loading, setLoading] = useState(false);
    const [progress, setProgress] = useState({ pct: 0, text: '' });
    const creepRef = useRef(null);
    const [stems, setStems] = useState(null);   // Map<key, { blob, name }>
    const [zipBlob, setZipBlob] = useState(null);
    const [error, setError] = useState('');
    const [status, setStatus] = useState('');
    const fileRef = useRef(null);
    const [dragover, setDragover] = useState(false);
    const [storageConfirmMsg, setStorageConfirmMsg] = useState(null);

    const handleFile = (f) => {
        setFile(f);
        setStems(null);
        setZipBlob(null);
        setError('');
        setStatus('');
    };

    const handleProcess = async () => {
        const msg = await checkBeforeProcess(STEM_LAYOUT.length);
        if (msg) { setStorageConfirmMsg(msg); return; }
        run();
    };

    const run = async () => {
        if (!file) return;
        window.dispatchEvent(new CustomEvent('waveform:stop-all'));
        setLoading(true);
        setError('');
        setStems(null);
        setZipBlob(null);
        setStatus('');
        clearInterval(creepRef.current);
        setProgress({ pct: 5, text: 'Validating audio format…' });

        try {
            const form = new FormData();
            form.append('audio', file);

            const token = await getToken();
            const headers = token ? { Authorization: `Bearer ${token}` } : {};

            let p = 5;
            creepRef.current = setInterval(() => {
                p = Math.min(p + 0.3, 78);
                const text = p < 15 ? 'Uploading audio file…'
                           : p < 28 ? 'Analyzing track structure…'
                           : p < 45 ? 'Separating main stems…'
                           : p < 62 ? 'Processing drum components…'
                           : p < 72 ? 'Generating one-shots…'
                           : 'Packaging results…';
                setProgress({ pct: p, text });
                if (p >= 78) clearInterval(creepRef.current);
            }, 450);

            const resp = await fetch(`${API_BASE}/separate`, { method: 'POST', body: form, headers });
            clearInterval(creepRef.current);

            if (!resp.ok) {
                const text = await resp.text();
                throw new Error(`API ${resp.status}: ${text}`);
            }

            setProgress({ pct: 82, text: 'Receiving stem bundle…' });

            const rawZip = await resp.arrayBuffer();
            setZipBlob(new Blob([rawZip], { type: 'application/zip' }));

            setProgress({ pct: 90, text: 'Extracting audio files…' });

            const zip = await JSZip.loadAsync(rawZip);
            const extracted = {};

            for (const [name, entry] of Object.entries(zip.files)) {
                if (entry.dir) continue;
                const stem = name.replace(/\.wav$/i, '');
                const blob = await entry.async('blob');
                extracted[stem] = { blob, name };
            }

            setProgress({ pct: 100, text: 'Separation complete!' });
            setStems(extracted);
            setStatus(`✅ Separated ${Object.keys(extracted).length} stems from ${file.name}`);
            localStorage.setItem('lastProcessedAt', String(Date.now()));
            window.dispatchEvent(new CustomEvent('audioProcessed'));
        } catch (e) {
            clearInterval(creepRef.current);
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    const downloadZip = () => {
        if (!zipBlob) return;
        const url = URL.createObjectURL(zipBlob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${file?.name?.replace(/\.\w+$/, '') || 'stems'}_stems.zip`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const renderGroup = (groupId) => {
        if (!stems) return null;
        const groupStems = STEM_LAYOUT.filter((s) => s.group === groupId && stems[s.key]);
        if (groupStems.length === 0) return null;

        const meta = GROUP_META[groupId];
        return (
            <div key={groupId} className="card fade-in" style={{ marginBottom: '1.5rem' }}>
                <h3 className="group-header" style={{ marginTop: 0 }}>
                    <span style={{ color: meta.color }}>{meta.title.split(' ')[0]}</span> {meta.title.split(' ').slice(1).join(' ')}
                </h3>
                {groupStems.map((s) => (
                    <WaveformPlayer
                        key={s.key}
                        label={s.label}
                        audioBlob={stems[s.key].blob}
                        fileName={stems[s.key].name}
                        color={meta.color}
                    />
                ))}
            </div>
        );
    };

    return (
        <div className="fade-in">
            <div className="card" style={{ marginBottom: '2rem' }}>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem', fontSize: '1.05rem' }}>
                    Upload a full track to perform high-fidelity stem separation. Our multi-stage pipeline extracts pristine vocals, isolates individual drum kit components, and generates production-ready one-shots.
                </p>

                {/* Upload + Run */}
                <div style={{ display: 'flex', gap: '2rem', alignItems: 'stretch', flexWrap: 'wrap' }}>
                    <div
                        className={`upload-zone ${dragover ? 'dragover' : ''}`}
                        style={{ flex: '1 1 400px' }}
                        onClick={() => fileRef.current?.click()}
                        onDragOver={(e) => { e.preventDefault(); setDragover(true); }}
                        onDragLeave={() => setDragover(false)}
                        onDrop={(e) => {
                            e.preventDefault();
                            setDragover(false);
                            if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
                        }}
                    >
                        <span className="icon">🎵</span>
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

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', flex: '0 0 auto', justifyContent: 'center' }}>
                        {storageConfirmMsg && (
                            <StorageConfirmModal
                                message={storageConfirmMsg}
                                onYes={() => { setStorageConfirmMsg(null); run(); }}
                                onNo={() => setStorageConfirmMsg(null)}
                            />
                        )}
                        <button
                            className="btn btn-primary"
                            disabled={!file || loading}
                            onClick={requireAuth(handleProcess)}
                            style={{ whiteSpace: 'nowrap', width: '100%' }}
                        >
                            {loading ? '⏳ Processing…' : '🔀 Separate Stems'}
                        </button>
                        {zipBlob && (
                            <button className="btn btn-secondary" onClick={downloadZip} style={{ width: '100%' }}>
                                📦 Download All (ZIP)
                            </button>
                        )}
                    </div>
                </div>

                {/* Progress */}
                {loading && (
                    <div style={{ marginTop: '1rem' }}>
                        <div className="progress-text loading-pulse">{progress.text}</div>
                        <div className="progress-bar-wrapper">
                            <div className="progress-bar-fill" style={{ width: `${progress.pct}%` }} />
                        </div>
                    </div>
                )}

                {/* Error / Status */}
                {error && <p className="status-error" style={{ marginTop: '1rem' }}>❌ {error}</p>}
                {status && <p className="status-success" style={{ marginTop: '1rem' }}>{status}</p>}

            </div>

            {/* Stem waveform players — grouped, full width */}
            {stems && (
                <div style={{ marginTop: '2rem' }}>
                    {['main', 'drums', 'oneshots'].map(renderGroup)}
                </div>
            )}
        </div>
    );
}
