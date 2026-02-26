import { useState, useRef } from 'react';

const API_BASE = '/api';

export default function AudioCutterTab() {
    const [file, setFile] = useState(null);
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');
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
            form.append('start', start || '0');
            form.append('end', end || '0');

            const resp = await fetch(`${API_BASE}/cut`, { method: 'POST', body: form });
            if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);

            const blob = await resp.blob();
            const name = `${file.name.replace(/\.\w+$/, '')}_trimmed.wav`;
            setResultUrl(URL.createObjectURL(blob));
            setResultName(name);
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
                    Trim audio to a specific time range. Set the start and end points in seconds, then cut.
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
                    <span className="icon">✂️</span>
                    <span className="label">Drop audio here or click to browse</span>
                    <span className="hint">WAV, FLAC, MP3, OGG, AIF</span>
                    {file && <span className="file-name">{file.name}</span>}
                    <input
                        ref={fileRef}
                        type="file"
                        hidden
                        accept=".wav,.flac,.mp3,.ogg,.aif,.aiff"
                        onChange={(e) => e.target.files[0] && handleFile(e.target.files[0])}
                    />
                </div>

                <div className="time-inputs" style={{ marginTop: '1.5rem' }}>
                    <div className="time-input-group">
                        <label className="field-label">Start Time (seconds)</label>
                        <input
                            className="text-input"
                            type="number"
                            min="0"
                            step="0.1"
                            placeholder="0.0"
                            value={start}
                            onChange={(e) => setStart(e.target.value)}
                        />
                    </div>
                    <div className="time-input-group">
                        <label className="field-label">End Time (seconds)</label>
                        <input
                            className="text-input"
                            type="number"
                            min="0"
                            step="0.1"
                            placeholder="End of file"
                            value={end}
                            onChange={(e) => setEnd(e.target.value)}
                        />
                    </div>
                </div>

                <button
                    className="btn btn-primary"
                    style={{ marginTop: '1.5rem', width: '100%', justifyContent: 'center' }}
                    disabled={!file || loading}
                    onClick={run}
                >
                    {loading ? '⏳ Cutting…' : '✂️ Cut Audio'}
                </button>

                {error && <p className="status-error" style={{ marginTop: '1rem' }}>❌ {error}</p>}

                {resultUrl && (
                    <div className="fade-in" style={{ marginTop: '1.5rem' }}>
                        <p className="status-success">✅ Audio trimmed successfully!</p>
                        <audio controls src={resultUrl} style={{ width: '100%', marginTop: '0.5rem' }} />
                        <button className="btn btn-secondary" style={{ marginTop: '1rem', width: '100%' }} onClick={download}>
                            ⬇ Download Trimmed Audio
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
