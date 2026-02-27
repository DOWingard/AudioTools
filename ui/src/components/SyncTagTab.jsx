import { useState, useRef } from 'react';
import JSZip from 'jszip';
import WaveformPlayer from './WaveformPlayer.jsx';
import { useRequireAuth } from '../AuthContext.jsx';

const API_BASE = '/api';

export default function SyncTagTab() {
    const requireAuth = useRequireAuth();
    const [file, setFile] = useState(null);
    const [isrc, setIsrc] = useState('');
    const [loading, setLoading] = useState(false);
    const [progress, setProgress] = useState({ pct: 0, text: '' });
    const [result, setResult] = useState(null);   // { meta, audioBlob, audioName, csvBlob, csvName }
    const [error, setError] = useState('');
    const fileRef = useRef(null);
    const [dragover, setDragover] = useState(false);

    const handleFile = (f) => {
        setFile(f);
        setResult(null);
        setError('');
    };

    const run = async () => {
        if (!file) return;
        setLoading(true);
        setError('');
        setResult(null);
        setProgress({ pct: 10, text: 'Uploading to compute service…' });

        try {
            const form = new FormData();
            form.append('audio', file);
            form.append('isrc', isrc.trim());

            const resp = await fetch(`${API_BASE}/tag`, { method: 'POST', body: form });

            if (!resp.ok) {
                const text = await resp.text();
                throw new Error(`API ${resp.status}: ${text}`);
            }

            setProgress({ pct: 80, text: 'Extracting results…' });

            const zip = await JSZip.loadAsync(await resp.arrayBuffer());

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
                if (/\.(wav|flac|mp3|ogg|aiff?)$/i.test(name)) {
                    audioBlob = await zip.files[name].async('blob');
                    audioName = name;
                    break;
                }
            }

            setProgress({ pct: 100, text: 'Done!' });
            setResult({ meta, audioBlob, audioName, csvBlob, csvName });
        } catch (e) {
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
                    Upload audio to auto-tag for sync licensing. Instantly analyze tracks through our LLM pipeline and receive a rich metadata summary, a tagged copy, and a CSV sidecar.
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
                            <span className="label">Drop audio here or click to browse</span>
                            <span className="hint">WAV, FLAC, MP3, AAC, AIF</span>
                            {file && <span className="file-name">{file.name}</span>}
                            <input
                                ref={fileRef}
                                type="file"
                                hidden
                                accept=".wav,.flac,.mp3,.aac,.aif,.aiff"
                                onChange={(e) => e.target.files[0] && handleFile(e.target.files[0])}
                            />
                        </div>

                        <div style={{ marginTop: '1.5rem' }}>
                            <label className="field-label">ISRC (optional)</label>
                            <input
                                className="text-input"
                                placeholder="e.g. GB-ABC-25-00001"
                                value={isrc}
                                onChange={(e) => setIsrc(e.target.value)}
                            />
                        </div>

                        <button
                            className="btn btn-primary"
                            style={{ marginTop: '1.5rem', width: '100%', justifyContent: 'center' }}
                            disabled={!file || loading}
                            onClick={requireAuth(run)}
                        >
                            {loading ? '⏳ Processing…' : '🚀 Analyze & Tag Track'}
                        </button>

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
    const llm = meta?.llm || {};
    const md = llm.metadata || {};
    const tags = meta?.tags || {};
    const info = meta?.audio_info || {};

    const pitch = llm.pitch;
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
            {pitch && (
                <>
                    <h2>Pitch</h2>
                    <p className="pitch-text">{pitch}</p>
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

            {comments && (
                <>
                    <h2>Sync Notes</h2>
                    <p className="pitch-text">{comments}</p>
                </>
            )}
        </div>
    );
}
