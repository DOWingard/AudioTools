import { useRef, useEffect, useState, useCallback } from 'react';
import WaveSurfer from 'wavesurfer.js';

// Module-level counter so each instance gets a stable unique ID.
let _instanceCounter = 0;
const PLAY_EVENT = 'waveform:play-started';
const STOP_ALL_EVENT = 'waveform:stop-all';

/**
 * WaveformPlayer — renders the ENTIRE waveform at full container width.
 * No horizontal scroll. WaveSurfer draws the complete waveform into a
 * canvas that matches the container's CSS width.
 *
 * Props:
 *   label    — display name (e.g. "🎤 Vocals")
 *   audioBlob — Blob of the audio (WAV)
 *   fileName — original filename for download
 *   color    — waveform fill color (optional)
 */
export default function WaveformPlayer({ label, audioBlob, fileName, color, autoPlay = false }) {
    const containerRef = useRef(null);
    const wsRef = useRef(null);
    const [playing, setPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [volume, setVolume] = useState(1);
    const [muted, setMuted] = useState(false);
    const prevVolumeRef = useRef(1);
    const autoPlayRef = useRef(autoPlay);
    autoPlayRef.current = autoPlay;
    // Stable ID for this instance — used to ignore our own broadcast.
    const instanceId = useRef(++_instanceCounter);

    // Pause this player when any other WaveformPlayer starts playing, or on stop-all.
    useEffect(() => {
        const id = instanceId.current;
        const onOtherPlay = (e) => { if (e.detail !== id) wsRef.current?.pause(); };
        const onStopAll = () => wsRef.current?.pause();
        window.addEventListener(PLAY_EVENT, onOtherPlay);
        window.addEventListener(STOP_ALL_EVENT, onStopAll);
        return () => {
            window.removeEventListener(PLAY_EVENT, onOtherPlay);
            window.removeEventListener(STOP_ALL_EVENT, onStopAll);
        };
    }, []);

    const waveColor = color || '#7c3aed';
    const progressColor = color ? lighten(color) : '#a78bfa';

    // Create / destroy wavesurfer
    useEffect(() => {
        if (!containerRef.current || !audioBlob) return;

        const ws = WaveSurfer.create({
            container: containerRef.current,
            waveColor,
            progressColor,
            cursorColor: '#c084fc',
            cursorWidth: 2,
            height: 56,
            barWidth: 2,
            barGap: 1,
            barRadius: 2,
            normalize: true,
            backend: 'WebAudio',
        });

        ws.loadBlob(audioBlob);

        ws.on('ready', () => {
            setDuration(ws.getDuration());
            ws.setVolume(volume);
            if (autoPlayRef.current) ws.play();
        });

        ws.on('audioprocess', () => {
            setCurrentTime(ws.getCurrentTime());
        });

        ws.on('seeking', () => {
            setCurrentTime(ws.getCurrentTime());
        });

        ws.on('finish', () => {
            setPlaying(false);
        });

        ws.on('play', () => {
            setPlaying(true);
            window.dispatchEvent(new CustomEvent(PLAY_EVENT, { detail: instanceId.current }));
        });
        ws.on('pause', () => setPlaying(false));

        wsRef.current = ws;

        return () => {
            ws.pause();
            ws.destroy();
            wsRef.current = null;
        };
    }, [audioBlob]); // eslint-disable-line react-hooks/exhaustive-deps

    const togglePlay = useCallback(() => {
        wsRef.current?.playPause();
    }, []);

    const handleVolumeChange = useCallback((e) => {
        const v = parseFloat(e.target.value);
        setVolume(v);
        setMuted(v === 0);
        if (wsRef.current) wsRef.current.setVolume(v);
    }, []);

    const toggleMute = useCallback(() => {
        if (muted) {
            const restored = prevVolumeRef.current || 1;
            setVolume(restored);
            setMuted(false);
            if (wsRef.current) wsRef.current.setVolume(restored);
        } else {
            prevVolumeRef.current = volume;
            setVolume(0);
            setMuted(true);
            if (wsRef.current) wsRef.current.setVolume(0);
        }
    }, [muted, volume]);

    const download = useCallback(() => {
        if (!audioBlob) return;
        const url = URL.createObjectURL(audioBlob);
        const a = document.createElement('a');
        a.href = url;
        a.download = fileName || 'stem.wav';
        a.click();
        URL.revokeObjectURL(url);
    }, [audioBlob, fileName]);

    const volumeIcon = muted || volume === 0 ? '🔇' : volume < 0.5 ? '🔉' : '🔊';

    return (
        <div className="waveform-player fade-in">
            <div className="player-header">
                <span className="stem-label">{label}</span>
                <div className="player-controls">
                    <button className="play-btn" onClick={togglePlay} title={playing ? 'Pause' : 'Play'}>
                        {playing ? '⏸' : '▶'}
                    </button>
                    <span className="time-display">
                        {fmtTime(currentTime)} / {fmtTime(duration)}
                    </span>
                    <button className="volume-btn" onClick={toggleMute} title={muted ? 'Unmute' : 'Mute'}>
                        {volumeIcon}
                    </button>
                    <input
                        className="volume-slider"
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={volume}
                        onChange={handleVolumeChange}
                        title={`Volume: ${Math.round(volume * 100)}%`}
                    />
                    <button className="download-btn" onClick={download} title="Download">
                        ⬇
                    </button>
                </div>
            </div>
            {/* FULL-WIDTH waveform container — no overflow scroll */}
            <div ref={containerRef} className="waveform-container" />
        </div>
    );
}

/* ── helpers ────────────────────────────────────────────────────────────── */

function fmtTime(sec) {
    if (!sec || !isFinite(sec)) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

function lighten(hex) {
    // Quick lighten for accent variants
    try {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        const l = (c) => Math.min(255, c + 60);
        return `#${l(r).toString(16).padStart(2, '0')}${l(g).toString(16).padStart(2, '0')}${l(b).toString(16).padStart(2, '0')}`;
    } catch {
        return '#a78bfa';
    }
}
