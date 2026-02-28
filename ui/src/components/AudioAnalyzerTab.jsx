import { useState, useRef } from 'react';
import { useAuth } from '@clerk/clerk-react';
import { useGatedRun } from '../AuthContext.jsx';

const API_BASE = '/api';

export default function AudioAnalyzerTab() {
    const requireAuth = useGatedRun();
    const { getToken } = useAuth();
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

            const token = await getToken();
            const headers = token ? { Authorization: `Bearer ${token}` } : {};
            const resp = await fetch(`${API_BASE}/analyze`, { method: 'POST', body: form, headers });
            if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);

            const data = await resp.json();
            setResult(data);
            localStorage.setItem('lastProcessedAt', String(Date.now()));
            window.dispatchEvent(new CustomEvent('audioProcessed'));
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    const energyBarWidth = result ? `${result.energy_rating * 10}%` : '0%';

    return (
        <div className="fade-in">
            <div className="card" style={{ marginBottom: '2rem' }}>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem', fontSize: '1.05rem' }}>
                    Get a comprehensive analysis of any audio file including BPM, key, loudness, dynamic range, spectral characteristics, and more.
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
                    <span className="icon">📊</span>
                    <span className="label">Drop audio here or click to browse</span>
                    <span className="hint">Any audio format supported</span>
                    {file && <span className="file-name">{file.name}</span>}
                    <input
                        ref={fileRef}
                        type="file"
                        hidden
                        accept="audio/*,.wav,.flac,.mp3,.aac,.aif,.aiff,.m4a"
                        onChange={(e) => e.target.files[0] && handleFile(e.target.files[0])}
                    />
                </div>

                <button
                    className="btn btn-primary"
                    style={{ marginTop: '1.5rem', width: '100%', justifyContent: 'center' }}
                    disabled={!file || loading}
                    onClick={requireAuth(run)}
                >
                    {loading ? '⏳ Analyzing…' : '📊 Analyze Audio'}
                </button>

                {error && <p className="status-error" style={{ marginTop: '1rem' }}>❌ {error}</p>}

                {result && (
                    <div className="fade-in" style={{ marginTop: '1.5rem' }}>
                        {/* Hero stats */}
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
                                {result.key_confidence != null && (
                                    <span className="result-label" style={{ fontSize: '0.65rem', marginTop: '0.25rem', opacity: 0.7 }}>
                                        {Math.round(result.key_confidence * 100)}% conf · {result.key_source}
                                    </span>
                                )}
                            </div>
                        </div>

                        {/* Energy bar */}
                        <div className="analyzer-section" style={{ marginTop: '1.5rem' }}>
                            <label className="field-label">Energy Rating</label>
                            <div className="energy-bar-container">
                                <div className="energy-bar-fill" style={{ width: energyBarWidth }}></div>
                                <span className="energy-bar-label">{result.energy_rating} / 10</span>
                            </div>
                        </div>

                        {/* Detail grid */}
                        <div className="result-grid" style={{ marginTop: '1.5rem' }}>
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
                            <div className="result-card">
                                <span className="result-icon">📢</span>
                                <span className="result-value">{result.lufs != null ? `${result.lufs} LUFS` : 'N/A'}</span>
                                <span className="result-label">Loudness</span>
                            </div>
                            <div className="result-card">
                                <span className="result-icon">📈</span>
                                <span className="result-value">{result.peak_db} dB</span>
                                <span className="result-label">Peak Level</span>
                            </div>
                            <div className="result-card">
                                <span className="result-icon">📉</span>
                                <span className="result-value">{result.rms_db} dB</span>
                                <span className="result-label">RMS Level</span>
                            </div>
                            <div className="result-card">
                                <span className="result-icon">✨</span>
                                <span className="result-value">{result.brightness_hz} Hz</span>
                                <span className="result-label">Brightness</span>
                            </div>
                        </div>

                        {/* Technical info */}
                        <div className="analyzer-section" style={{ marginTop: '1.5rem' }}>
                            <label className="field-label">Technical Details</label>
                            <div className="result-grid">
                                <div className="result-card">
                                    <span className="result-value">{result.sample_rate ? `${result.sample_rate} Hz` : 'N/A'}</span>
                                    <span className="result-label">Sample Rate</span>
                                </div>
                                <div className="result-card">
                                    <span className="result-value">{result.channels || 'N/A'}</span>
                                    <span className="result-label">Channels</span>
                                </div>
                                <div className="result-card">
                                    <span className="result-value">{result.bit_depth || 'N/A'}</span>
                                    <span className="result-label">Bit Depth</span>
                                </div>
                                <div className="result-card">
                                    <span className="result-value">{result.codec || 'N/A'}</span>
                                    <span className="result-label">Codec</span>
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
