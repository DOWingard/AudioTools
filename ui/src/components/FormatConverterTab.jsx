import { useState, useRef } from 'react';
import { useAuth } from '@clerk/clerk-react';
import { useGatedRun } from '../AuthContext.jsx';

const API_BASE = '/api';

const FORMATS = [
    { id: 'mp3', label: 'MP3', desc: 'Universal, lossy' },
    { id: 'wav', label: 'WAV', desc: 'Lossless, large' },
    { id: 'flac', label: 'FLAC', desc: 'Lossless, compressed' },
];

export default function FormatConverterTab() {
    const requireAuth = useGatedRun();
    const { getToken } = useAuth();
    const [file, setFile] = useState(null);
    const [format, setFormat] = useState('mp3');
    const [loading, setLoading] = useState(false);
    const [resultUrl, setResultUrl] = useState(null);
    const [resultName, setResultName] = useState('');
    const [error, setError] = useState('');
    const fileRef = useRef(null);
    const [dragover, setDragover] = useState(false);

    const handleFile = (f) => {
        setFile(f);
        setResultUrl(null);
        setError('');
    };

    const run = async () => {
        if (!file) return;
        setLoading(true);
        setError('');
        setResultUrl(null);

        try {
            const form = new FormData();
            form.append('audio', file);
            form.append('format', format);

            const token = await getToken();
            const headers = token ? { Authorization: `Bearer ${token}` } : {};
            const resp = await fetch(`${API_BASE}/convert`, { method: 'POST', body: form, headers });
            if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);

            const blob = await resp.blob();
            const ext = format;
            const name = `${file.name.replace(/\.\w+$/, '')}.${ext}`;
            setResultUrl(URL.createObjectURL(blob));
            setResultName(name);
            localStorage.setItem('lastProcessedAt', String(Date.now()));
            window.dispatchEvent(new CustomEvent('audioProcessed'));
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    const download = () => {
        if (!resultUrl) return;
        const a = document.createElement('a');
        a.href = resultUrl;
        a.download = resultName;
        a.click();
    };

    return (
        <div className="fade-in">
            <div className="card" style={{ marginBottom: '2rem' }}>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem', fontSize: '1.05rem' }}>
                    Convert audio files between formats. Upload any audio file and select the target format.
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
                    <span className="icon">🔄</span>
                    <span className="label">Drop audio here or click to browse</span>
                    <span className="hint">Any audio format supported</span>
                    {file && <span className="file-name">{file.name}</span>}
                    <input
                        ref={fileRef}
                        type="file"
                        hidden
                        accept="audio/*,.wav,.flac,.mp3,.aac,.aif,.aiff,.m4a,.wma,.aac"
                        onChange={(e) => e.target.files[0] && handleFile(e.target.files[0])}
                    />
                </div>

                <div style={{ marginTop: '1.5rem' }}>
                    <label className="field-label">Output Format</label>
                    <div className="format-picker">
                        {FORMATS.map((f) => (
                            <button
                                key={f.id}
                                className={`format-option ${format === f.id ? 'active' : ''}`}
                                onClick={() => setFormat(f.id)}
                            >
                                <span className="format-label">{f.label}</span>
                                <span className="format-desc">{f.desc}</span>
                            </button>
                        ))}
                    </div>
                </div>

                <button
                    className="btn btn-primary"
                    style={{ marginTop: '1.5rem', width: '100%', justifyContent: 'center' }}
                    disabled={!file || loading}
                    onClick={requireAuth(run)}
                >
                    {loading ? '⏳ Converting…' : `🔄 Convert to ${format.toUpperCase()}`}
                </button>

                {error && <p className="status-error" style={{ marginTop: '1rem' }}>❌ {error}</p>}

                {resultUrl && (
                    <div className="fade-in" style={{ marginTop: '1.5rem' }}>
                        <p className="status-success">✅ Converted to {format.toUpperCase()} successfully!</p>
                        <button className="btn btn-secondary" style={{ marginTop: '0.5rem', width: '100%' }} onClick={download}>
                            ⬇ Download {resultName}
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
