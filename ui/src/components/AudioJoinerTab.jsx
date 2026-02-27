import { useState, useRef } from 'react';
import { useRequireAuth } from '../AuthContext.jsx';

const API_BASE = '/api';

export default function AudioJoinerTab() {
    const requireAuth = useRequireAuth();
    const [files, setFiles] = useState([]);
    const [loading, setLoading] = useState(false);
    const [resultUrl, setResultUrl] = useState(null);
    const [error, setError] = useState('');
    const fileRef = useRef(null);

    const addFiles = (newFiles) => {
        setFiles((prev) => [...prev, ...Array.from(newFiles)]);
        setResultUrl(null);
        setError('');
    };

    const removeFile = (idx) => {
        setFiles((prev) => prev.filter((_, i) => i !== idx));
    };

    const moveFile = (idx, dir) => {
        setFiles((prev) => {
            const arr = [...prev];
            const target = idx + dir;
            if (target < 0 || target >= arr.length) return arr;
            [arr[idx], arr[target]] = [arr[target], arr[idx]];
            return arr;
        });
    };

    const run = async () => {
        if (files.length < 2) return;
        setLoading(true);
        setError('');
        setResultUrl(null);

        try {
            const form = new FormData();
            files.forEach((f) => form.append('audio', f));

            const resp = await fetch(`${API_BASE}/join`, { method: 'POST', body: form });
            if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);

            const blob = await resp.blob();
            setResultUrl(URL.createObjectURL(blob));
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
        a.download = 'joined.wav';
        a.click();
    };

    return (
        <div className="fade-in">
            <div className="card" style={{ marginBottom: '2rem' }}>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem', fontSize: '1.05rem' }}>
                    Upload multiple audio files and join them together in the order listed below.
                </p>

                <div
                    className="upload-zone"
                    onClick={() => fileRef.current?.click()}
                >
                    <span className="icon">🔗</span>
                    <span className="label">Click to add audio files</span>
                    <span className="hint">WAV, FLAC, MP3, AAC, AIF — add multiple</span>
                    <input
                        ref={fileRef}
                        type="file"
                        hidden
                        multiple
                        accept=".wav,.flac,.mp3,.aac,.aif,.aiff"
                        onChange={(e) => {
                            if (e.target.files.length) addFiles(e.target.files);
                            e.target.value = '';
                        }}
                    />
                </div>

                {files.length > 0 && (
                    <div className="file-list" style={{ marginTop: '1.5rem' }}>
                        <label className="field-label">Files (in join order)</label>
                        {files.map((f, i) => (
                            <div key={`${f.name}-${i}`} className="file-list-item">
                                <span className="file-list-number">{i + 1}</span>
                                <span className="file-list-name">{f.name}</span>
                                <span className="file-list-size">{(f.size / 1024 / 1024).toFixed(1)} MB</span>
                                <div className="file-list-actions">
                                    <button
                                        className="file-list-btn"
                                        onClick={() => moveFile(i, -1)}
                                        disabled={i === 0}
                                        title="Move up"
                                    >▲</button>
                                    <button
                                        className="file-list-btn"
                                        onClick={() => moveFile(i, 1)}
                                        disabled={i === files.length - 1}
                                        title="Move down"
                                    >▼</button>
                                    <button
                                        className="file-list-btn file-list-btn-remove"
                                        onClick={() => removeFile(i)}
                                        title="Remove"
                                    >✕</button>
                                </div>
                            </div>
                        ))}
                    </div>
                )}

                <button
                    className="btn btn-primary"
                    style={{ marginTop: '1.5rem', width: '100%', justifyContent: 'center' }}
                    disabled={files.length < 2 || loading}
                    onClick={requireAuth(run)}
                >
                    {loading ? '⏳ Joining…' : `🔗 Join ${files.length} Files`}
                </button>

                {error && <p className="status-error" style={{ marginTop: '1rem' }}>❌ {error}</p>}

                {resultUrl && (
                    <div className="fade-in" style={{ marginTop: '1.5rem' }}>
                        <p className="status-success">✅ Audio files joined successfully!</p>
                        <audio controls src={resultUrl} style={{ width: '100%', marginTop: '0.5rem' }} />
                        <button className="btn btn-secondary" style={{ marginTop: '1rem', width: '100%' }} onClick={download}>
                            ⬇ Download Joined Audio
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
