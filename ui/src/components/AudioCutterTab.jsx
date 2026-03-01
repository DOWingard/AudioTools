import { useState, useRef, useEffect, useCallback } from 'react';
import WaveSurfer from 'wavesurfer.js';
import RegionsPlugin from 'wavesurfer.js/dist/plugins/regions.esm.js';
import { useAuth } from '@clerk/clerk-react';
import { useGatedRun } from '../AuthContext.jsx';
import WaveformPlayer from './WaveformPlayer.jsx';

let _cutterInstanceId = 0;

const API_BASE = '/api';

function fmtTime(sec) {
    if (!sec || !isFinite(sec)) return '0:00.0';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    const ms = Math.floor((sec % 1) * 10);
    return `${m}:${s.toString().padStart(2, '0')}.${ms}`;
}

export default function AudioCutterTab() {
    const requireAuth = useGatedRun();
    const { getToken } = useAuth();
    const [file, setFile] = useState(null);
    const [audioBlob, setAudioBlob] = useState(null);
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');
    const [duration, setDuration] = useState(0);
    const [playing, setPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [loading, setLoading] = useState(false);
    const [resultBlob, setResultBlob] = useState(null);
    const [resultName, setResultName] = useState('');
    const cutterIdRef = useRef(++_cutterInstanceId);
    const [error, setError] = useState('');
    const fileRef = useRef(null);
    const [dragover, setDragover] = useState(false);

    const waveContainerRef = useRef(null);
    const wsRef = useRef(null);
    const regionsRef = useRef(null);
    const regionRef = useRef(null);
    const updatingFromRegion = useRef(false);
    const updatingFromInput = useRef(false);

    const handleFile = useCallback((f) => {
        setFile(f);
        setAudioBlob(f);
        setResultBlob(null);
        setError('');
        setStart('');
        setEnd('');
        setDuration(0);
        setPlaying(false);
        setCurrentTime(0);
    }, []);

    // ── WaveSurfer + Regions setup ────────────────────────────────────
    useEffect(() => {
        if (!waveContainerRef.current || !audioBlob) return;

        // Destroy previous instance
        if (wsRef.current) {
            wsRef.current.destroy();
            wsRef.current = null;
            regionsRef.current = null;
            regionRef.current = null;
        }

        const regions = RegionsPlugin.create();
        regionsRef.current = regions;

        const ws = WaveSurfer.create({
            container: waveContainerRef.current,
            waveColor: 'rgba(0, 0, 0, 0.18)',
            progressColor: 'rgba(230, 57, 70, 0.45)',
            cursorColor: '#e63946',
            cursorWidth: 2,
            height: 120,
            barWidth: 2,
            barGap: 1,
            barRadius: 2,
            normalize: true,
            backend: 'WebAudio',
            plugins: [regions],
        });

        ws.loadBlob(audioBlob);

        ws.on('ready', () => {
            const dur = ws.getDuration();
            setDuration(dur);
            setStart('0');
            setEnd(dur.toFixed(1));

            // Create the initial region spanning the full file
            const region = regions.addRegion({
                start: 0,
                end: dur,
                color: 'rgba(230, 57, 70, 0.15)',
                drag: false,
                resize: true,
            });
            regionRef.current = region;

            region.on('update-end', () => {
                updatingFromRegion.current = true;
                setStart(region.start.toFixed(1));
                setEnd(region.end.toFixed(1));
                setTimeout(() => { updatingFromRegion.current = false; }, 50);
            });

            region.on('update', () => {
                updatingFromRegion.current = true;
                setStart(region.start.toFixed(1));
                setEnd(region.end.toFixed(1));
            });
        });

        ws.on('audioprocess', () => setCurrentTime(ws.getCurrentTime()));
        ws.on('seeking', () => setCurrentTime(ws.getCurrentTime()));
        ws.on('finish', () => setPlaying(false));
        ws.on('play', () => {
            setPlaying(true);
            // Broadcast so all other players (WaveformPlayer, etc.) pause.
            window.dispatchEvent(new CustomEvent('waveform:play-started', { detail: cutterIdRef.current }));
        });
        ws.on('pause', () => setPlaying(false));

        // Stop this WaveSurfer when any other player starts or stop-all fires.
        const stopHandler = () => { if (ws.isPlaying()) ws.pause(); };
        const playHandler = (e) => { if (e.detail !== cutterIdRef.current && ws.isPlaying()) ws.pause(); };
        window.addEventListener('waveform:play-started', playHandler);
        window.addEventListener('waveform:stop-all', stopHandler);

        wsRef.current = ws;

        return () => {
            window.removeEventListener('waveform:play-started', playHandler);
            window.removeEventListener('waveform:stop-all', stopHandler);
            ws.destroy();
            wsRef.current = null;
            regionsRef.current = null;
            regionRef.current = null;
        };
    }, [audioBlob]);

    // ── Sync input fields → region ────────────────────────────────────
    useEffect(() => {
        if (updatingFromRegion.current) return;
        if (!regionRef.current || !duration) return;

        const s = parseFloat(start);
        const e = parseFloat(end);
        if (isNaN(s) || isNaN(e)) return;

        const clampedStart = Math.max(0, Math.min(s, duration));
        const clampedEnd = Math.max(clampedStart + 0.1, Math.min(e, duration));

        updatingFromInput.current = true;
        regionRef.current.setOptions({ start: clampedStart, end: clampedEnd });
        setTimeout(() => { updatingFromInput.current = false; }, 50);
    }, [start, end, duration]);

    // ── Preview: play only selected region ────────────────────────────
    const previewSelection = useCallback(() => {
        if (!wsRef.current || !regionRef.current) return;
        const region = regionRef.current;
        // If already playing, pause
        if (playing) {
            wsRef.current.pause();
            return;
        }
        region.play();
    }, [playing]);

    // ── Cut API call ──────────────────────────────────────────────────
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

            const token = await getToken();
            const headers = token ? { Authorization: `Bearer ${token}` } : {};
            const resp = await fetch(`${API_BASE}/cut`, { method: 'POST', body: form, headers });
            if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);

            const blob = await resp.blob();
            const name = `${file.name.replace(/\.\w+$/, '')}_trimmed.wav`;
            setResultBlob(blob);
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
        if (!resultBlob) return;
        const url = URL.createObjectURL(resultBlob);
        const a = document.createElement('a');
        a.href = url;
        a.download = resultName;
        a.click();
        URL.revokeObjectURL(url);
    };

    const selectionDuration = (() => {
        const s = parseFloat(start);
        const e = parseFloat(end);
        if (isNaN(s) || isNaN(e) || e <= s) return null;
        return (e - s).toFixed(1);
    })();

    return (
        <div className="fade-in">
            <div className="card" style={{ marginBottom: '2rem' }}>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem', fontSize: '1.05rem' }}>
                    Trim audio to a specific time range. Drop a file to visualise the waveform, drag the selection handles, then cut.
                </p>

                {/* ── Upload Zone ──────────────────────────── */}
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
                    <span className="hint">WAV, FLAC, MP3, AAC, AIF</span>
                    {file && <span className="file-name">{file.name}</span>}
                    <input
                        ref={fileRef}
                        type="file"
                        hidden
                        accept="audio/*,.wav,.flac,.mp3,.aac,.aif,.aiff"
                        onChange={(e) => e.target.files[0] && handleFile(e.target.files[0])}
                    />
                </div>

                {/* ── Waveform + Region ────────────────────── */}
                {audioBlob && (
                    <div className="cutter-waveform-section fade-in" style={{ marginTop: '1.5rem' }}>
                        <div className="cutter-waveform-wrap">
                            <div ref={waveContainerRef} className="cutter-waveform-canvas" />
                        </div>

                        {/* Time info bar */}
                        <div className="cutter-time-bar">
                            <span className="cutter-time-tag">
                                <span className="cutter-time-label">Start</span>
                                <span className="cutter-time-value">{fmtTime(parseFloat(start) || 0)}</span>
                            </span>
                            {selectionDuration && (
                                <span className="cutter-time-tag cutter-time-duration">
                                    <span className="cutter-time-label">Selection</span>
                                    <span className="cutter-time-value">{selectionDuration}s</span>
                                </span>
                            )}
                            <span className="cutter-time-tag">
                                <span className="cutter-time-label">End</span>
                                <span className="cutter-time-value">{fmtTime(parseFloat(end) || 0)}</span>
                            </span>
                        </div>

                        {/* Preview button */}
                        <button
                            className="btn btn-secondary"
                            style={{ marginTop: '0.75rem', width: '100%', justifyContent: 'center' }}
                            onClick={previewSelection}
                            disabled={!duration}
                        >
                            {playing ? '⏸ Pause Preview' : '▶ Preview Selection'}
                        </button>
                    </div>
                )}

                {/* ── Manual Time Inputs ───────────────────── */}
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

                {/* ── Cut Button ───────────────────────────── */}
                <button
                    className="btn btn-primary"
                    style={{ marginTop: '1.5rem', width: '100%', justifyContent: 'center' }}
                    disabled={!file || loading}
                    onClick={requireAuth(run)}
                >
                    {loading ? '⏳ Cutting…' : '✂️ Cut Audio'}
                </button>

                {error && <p className="status-error" style={{ marginTop: '1rem' }}>❌ {error}</p>}

                {resultBlob && (
                    <div className="fade-in" style={{ marginTop: '1.5rem' }}>
                        <p className="status-success">✅ Audio trimmed successfully!</p>
                        <WaveformPlayer
                            label="✂️ Trimmed Audio"
                            audioBlob={resultBlob}
                            fileName={resultName}
                            color="#e63946"
                            autoPlay={true}
                        />
                        <button className="btn btn-secondary" style={{ marginTop: '1rem', width: '100%' }} onClick={download}>
                            ⬇ Download Trimmed Audio
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
