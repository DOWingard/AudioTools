import { useState, useRef } from 'react';
import JSZip from 'jszip';
import { useAuth } from '@clerk/clerk-react';
import WaveformPlayer from './WaveformPlayer.jsx';
import { useAuthContext, useGatedRun, useStorageGuard, useConsumeUsage } from '../AuthContext.jsx';
import StorageConfirmModal from './StorageConfirmModal.jsx';

const API_BASE = '/api';

export default function SyncTagTab() {
    const requireAuth = useGatedRun();
    const { checkBeforeProcess } = useStorageGuard();
    const consumeUsage = useConsumeUsage();
    const { profile, setIsProcessing } = useAuthContext();
    const { getToken } = useAuth();
    const [file, setFile] = useState(null);
    const [title, setTitle] = useState('');
    const [artist, setArtist] = useState('');
    const [album, setAlbum] = useState('');
    const [isrc, setIsrc] = useState('');
    const [bpm, setBpm] = useState('');
    const [genre, setGenre] = useState('');
    const [loading, setLoading] = useState(false);
    const [progress, setProgress] = useState({ pct: 0, text: '' });
    const creepRef = useRef(null);
    const [result, setResult] = useState(null);   // { meta, audioBlob, audioName, csvBlob, csvName }
    const [error, setError] = useState('');
    const fileRef = useRef(null);
    const [dragover, setDragover] = useState(false);
    const [storageConfirmMsg, setStorageConfirmMsg] = useState(null);

    useEffect(() => {
        setIsProcessing(loading);
        return () => setIsProcessing(false);
    }, [loading, setIsProcessing]);

    const handleFile = (f) => {
        setFile(f);
        setResult(null);
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
        setResult(null);
        clearInterval(creepRef.current);
        setProgress({ pct: 5, text: 'Validating audio format…' });

        try {
            const form = new FormData();
            form.append('audio', file);
            form.append('title', title.trim());
            form.append('artist', artist.trim());
            form.append('album', album.trim());
            form.append('isrc', isrc.trim());
            form.append('bpm', bpm.trim());
            form.append('genre', genre.trim());

            const token = await getToken();
            const headers = token ? { Authorization: `Bearer ${token}` } : {};

            let p = 5;
            creepRef.current = setInterval(() => {
                p = Math.min(p + 0.28, 76);
                const text = p < 18 ? 'Uploading audio file…'
                    : p < 32 ? 'Analyzing audio characteristics…'
                        : p < 47 ? 'Identifying musical elements…'
                            : p < 60 ? 'Classifying genre, mood, and tempo…'
                                : p < 72 ? 'Generating sync licensing metadata…'
                                    : 'Writing metadata tags…';
                setProgress({ pct: p, text });
                if (p >= 76) clearInterval(creepRef.current);
            }, 500);

            const resp = await fetch(`${API_BASE}/tag`, { method: 'POST', body: form, headers });
            clearInterval(creepRef.current);

            if (!resp.ok) {
                const text = await resp.text();
                throw new Error(`API ${resp.status}: ${text}`);
            }
            await consumeUsage();

            setProgress({ pct: 80, text: 'Receiving tagged files…' });

            const rawZip = await resp.arrayBuffer();

            setProgress({ pct: 88, text: 'Parsing metadata…' });

            const zip = await JSZip.loadAsync(rawZip);

            // metadata.json
            const metaFile = zip.file('metadata.json');
            const meta = metaFile ? JSON.parse(await metaFile.async('string')) : {};

            // CSV
            let csvBlob = null, csvName = null;
            for (const name of Object.keys(zip.files)) {
                if (name.endsWith('.csv')) {
                    csvBlob = await zip.files[name].async('blob');
                    csvName = name;
                    break;
                }
            }

            // Tagged audio
            let audioBlob = null, audioName = null;
            for (const name of Object.keys(zip.files)) {
                if (/\.(wav|flac|mp3|ogg|m4a|aac)$/i.test(name)) {
                    audioBlob = await zip.files[name].async('blob');
                    audioName = name;
                    break;
                }
            }

            setProgress({ pct: 100, text: 'Analysis complete!' });
            setResult({ meta, audioBlob, audioName, csvBlob, csvName });
            localStorage.setItem('lastProcessedAt', String(Date.now()));
            window.dispatchEvent(new CustomEvent('audioProcessed'));
        } catch (e) {
            clearInterval(creepRef.current);
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    const downloadBlob = (blob, name) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        a.click();
        URL.revokeObjectURL(url);
    };

    return (
        <div className="fade-in">
            <div className="card" style={{ marginBottom: '2rem' }}>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem', fontSize: '1.05rem' }}>
                    Upload audio to auto-tag for sync licensing. Instantly analyze tracks and receive a rich metadata summary, a tagged copy, and a CSV sidecar.
                </p>

                <div className="two-col">
                    {/* Left column — inputs */}
                    <div>
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
                            <span className="icon">🎵</span>
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

                        <div style={{ marginTop: '1.5rem', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                            <div>
                                <label className="field-label">Title (optional)</label>
                                <input
                                    className="text-input"
                                    placeholder="Song title"
                                    value={title}
                                    onChange={(e) => setTitle(e.target.value)}
                                    disabled={loading}
                                />
                            </div>
                            <div>
                                <label className="field-label">Artist (optional)</label>
                                <input
                                    className="text-input"
                                    placeholder="Artist name"
                                    value={artist}
                                    onChange={(e) => setArtist(e.target.value)}
                                    disabled={loading}
                                />
                            </div>
                            <div>
                                <label className="field-label">Album (optional)</label>
                                <input
                                    className="text-input"
                                    placeholder="Album name"
                                    value={album}
                                    onChange={(e) => setAlbum(e.target.value)}
                                    disabled={loading}
                                />
                            </div>
                            <div>
                                <label className="field-label">Genre override (optional)</label>
                                <input
                                    className="text-input"
                                    placeholder="e.g. Electronic"
                                    value={genre}
                                    onChange={(e) => setGenre(e.target.value)}
                                    disabled={loading}
                                />
                            </div>
                            <div>
                                <label className="field-label">BPM override (optional)</label>
                                <input
                                    className="text-input"
                                    type="number"
                                    min="1"
                                    max="999"
                                    placeholder="e.g. 128"
                                    value={bpm}
                                    onChange={(e) => setBpm(e.target.value)}
                                    disabled={loading}
                                />
                            </div>
                            <div>
                                <label className="field-label">ISRC (optional)</label>
                                <input
                                    className="text-input"
                                    placeholder="e.g. GB-ABC-25-00001"
                                    value={isrc}
                                    onChange={(e) => setIsrc(e.target.value)}
                                    disabled={loading}
                                />
                            </div>
                        </div>

                        <button
                            className="btn btn-primary"
                            style={{ marginTop: '1.5rem', width: '100%', justifyContent: 'center' }}
                            disabled={!file || loading}
                            onClick={requireAuth(handleProcess)}
                        >
                            {loading ? '⏳ Processing…' : '🚀 Analyze & Tag Track'}
                        </button>

                        {storageConfirmMsg && (
                            <StorageConfirmModal
                                message={storageConfirmMsg}
                                onYes={() => { setStorageConfirmMsg(null); run(); }}
                                onNo={() => setStorageConfirmMsg(null)}
                            />
                        )}

                        {loading && (
                            <div style={{ marginTop: '1.5rem' }}>
                                <div className="progress-text loading-pulse">{progress.text}</div>
                                <div className="progress-bar-wrapper">
                                    <div className="progress-bar-fill" style={{ width: `${progress.pct}%` }} />
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Right column — results */}
                    <div>
                        {error && <p className="status-error">❌ {error}</p>}

                        {result && (
                            <div className="fade-in">
                                <SummaryCard meta={result.meta} />

                                {result.audioBlob && (
                                    <div style={{ marginTop: '1.5rem' }}>
                                        <WaveformPlayer
                                            label="🎧 Tagged Audio"
                                            audioBlob={result.audioBlob}
                                            fileName={result.audioName}
                                            autoPlay={true}
                                        />
                                    </div>
                                )}

                                <div className="download-row">
                                    {result.audioBlob && (
                                        <button
                                            className="btn btn-secondary"
                                            style={{ flex: 1 }}
                                            onClick={() => downloadBlob(result.audioBlob, result.audioName)}
                                        >
                                            ⬇ Tagged Audio
                                        </button>
                                    )}
                                    {result.csvBlob && (
                                        <button
                                            className="btn btn-secondary"
                                            style={{ flex: 1 }}
                                            onClick={() => downloadBlob(result.csvBlob, result.csvName)}
                                        >
                                            ⬇ CSV Sidecar
                                        </button>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}


/* ── Summary card sub-component ────────────────────────────────────────── */

function SummaryCard({ meta }) {
    const md = meta?.metadata || {};
    const tags = meta?.tags || {};
    const info = meta?.audio_info || {};

    const comments = md.Comments;
    const genre = md.Genre || (tags.genre || [''])[0];
    const mood = Array.isArray(md.Mood || tags.mood)
        ? (md.Mood || tags.mood).join('; ')
        : md.Mood || '';
    const tempo = md.Tempo || tags.tempo || '';
    const energy = md.Energy || '';
    const vocal = md.Vocal || '';
    const trackType = md.Track_Type || '';
    const dur = info.duration;
    const isrcVal = md.ISRC || '';

    return (
        <div className="summary-card">
            {comments && (
                <>
                    <h2>Sync Notes</h2>
                    <p className="pitch-text">{comments}</p>
                </>
            )}

            <h2>Metadata</h2>
            <ul>
                <li><strong>Genre:</strong> {genre}</li>
                <li><strong>Mood:</strong> {mood}</li>
                <li><strong>Tempo:</strong> {tempo}</li>
                <li><strong>Energy:</strong> {energy}/10</li>
                <li><strong>Vocal:</strong> {vocal}</li>
                <li><strong>Track Type:</strong> {trackType}</li>
                {dur && <li><strong>Duration:</strong> {typeof dur === 'number' ? dur.toFixed(1) : dur}s</li>}
                {isrcVal && <li><strong>ISRC:</strong> {isrcVal}</li>}
            </ul>

        </div>
    );
}
