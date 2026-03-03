import { useState, useRef } from 'react';

const API_BASE = '/api';

export default function BpmKeyFinderTab() {
    const [file, setFile] = useState(null);
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState(null);
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

        try {
            const form = new FormData();
            form.append('audio', file);

            const resp = await fetch(`${API_BASE}/bpm-key`, { method: 'POST', body: form });
            if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);

            const data = await resp.json();
            setResult(data);
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="fade-in">
            <div className="card" style={{ marginBottom: '2rem' }}>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem', fontSize: '1.05rem' }}>
                    Detect the BPM (tempo) and musical key of any audio track using AI beat tracking and chroma analysis.
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

                <button
                    className="btn btn-primary"
                    style={{ marginTop: '1.5rem', width: '100%', justifyContent: 'center' }}
                    disabled={!file || loading}
                    onClick={run}
                >
                    {loading ? '⏳ Analyzing…' : '🔍 Detect BPM & Key'}
                </button>

                {error && <p className="status-error" style={{ marginTop: '1rem' }}>❌ {error}</p>}

                {result && (
                    <div className="fade-in result-dashboard" style={{ marginTop: '1.5rem' }}>
                        <div className="result-grid">
                            <div className="result-card result-card-hero">
                                <span className="result-icon">⏱️</span>
                                <span className="result-value">{result.bpm}</span>
                                <span className="result-label">BPM</span>
                            </div>
                            <div className="result-card result-card-hero">
                                <span className="result-icon">🎹</span>
                                <span className="result-value">{result.key}</span>
                                <span className="result-label">Musical Key</span>
                            </div>
                            <div className="result-card">
                                <span className="result-icon">🏃</span>
                                <span className="result-value">{result.tempo_category}</span>
                                <span className="result-label">Tempo</span>
                            </div>
                            <div className="result-card">
                                <span className="result-icon">⏱️</span>
                                <span className="result-value">{result.duration}s</span>
                                <span className="result-label">Duration</span>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
