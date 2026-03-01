/**
 * MyFilesTab — Library of the user's stored audio files.
 *
 * Sub-tabs:
 *   "Saved Files" — grouped list (standard + premium)
 *   "Smart Graph" — 3D force graph with similarity search (premium only)
 *
 * Shared state:
 *   nowPlaying — the file currently loaded in the NowPlaying bar
 *
 * Graph highlights accumulate: each search batch adds to the highlighted set,
 * growing the cluster visible in the graph.
 */

import { useState, useEffect, useRef, useCallback, useMemo, Suspense, lazy } from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from '@clerk/clerk-react';
import * as THREE from 'three';
import WaveformPlayer from './WaveformPlayer.jsx';
import { useAuthContext } from '../AuthContext.jsx';

// Lazy-load ForceGraph3D — it pulls in Three.js and ~500KB of WebGL code
const ForceGraph3D = lazy(() => import('react-force-graph-3d'));

const API_BASE = '/api';

/* ─── Constants ────────────────────────────────────────────────────────────── */

const PROCESS_META = {
    separate: { label: 'Stem Separator', icon: '🎛️', color: '#7c3aed' },
    tag: { label: 'SyncTag', icon: '🏷️', color: '#e63946' },
    karaoke: { label: 'Vocal Remover', icon: '🎤', color: '#2563eb' },
    cut: { label: 'Audio Cutter', icon: '✂️', color: '#059669' },
    join: { label: 'Audio Joiner', icon: '🔗', color: '#d97706' },
    convert: { label: 'Format Converter', icon: '🔄', color: '#0891b2' },
    analyze: { label: 'Track Analyzer', icon: '📊', color: '#f59e0b' },
    upload: { label: 'Uploaded', icon: '📤', color: '#6366f1' },
};

const SUBGROUP_META = {
    main: { label: 'Main Stems', color: '#7c3aed' },
    drums: { label: 'Drum Stems', color: '#2563eb' },
    oneshots: { label: 'One-Shots', color: '#059669' },
    tagged: { label: 'Tagged Track', color: '#e63946' },
    instrumental: { label: 'Instrumental', color: '#2563eb' },
    trimmed: { label: 'Trimmed', color: '#059669' },
    joined: { label: 'Joined', color: '#d97706' },
    converted: { label: 'Converted', color: '#0891b2' },
    analyzed: { label: 'Analyzed', color: '#f59e0b' },
    uploaded: { label: 'Uploaded', color: '#6366f1' },
};

const PROCESS_SUBGROUP_ORDER = {
    separate: ['main', 'drums', 'oneshots'],
    tag: ['tagged'],
    karaoke: ['instrumental'],
    cut: ['trimmed'],
    join: ['joined'],
    convert: ['converted'],
    analyze: ['analyzed'],
    upload: ['uploaded'],
};

/* ─── Helpers ───────────────────────────────────────────────────────────────── */

function fmtDate(iso) {
    if (!iso) return '';
    try {
        return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    } catch { return ''; }
}

function fmtDur(secs) {
    if (!secs) return '';
    const m = Math.floor(secs / 60), s = Math.round(secs % 60);
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function fmtSize(bytes) {
    if (!bytes) return '';
    return bytes < 1048576 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}

function nodeColor(pt) { return (PROCESS_META[pt] || { color: '#888' }).color; }

function linkColor(sim) {
    if (sim >= 0.85) return 'rgba(0,255,136,0.75)';
    if (sim >= 0.70) return 'rgba(0,200,255,0.55)';
    if (sim >= 0.55) return 'rgba(140,80,255,0.40)';
    return 'rgba(80,40,160,0.20)';
}

/* ─── Three.js node factory (called by nodeThreeObject) ──────────────────── */

function makeNodeObj(node, isHighlighted, isPlaying) {
    const color = nodeColor(node.process_type);
    const size = Math.max(3.5, Math.min(9, 3.5 + (node.duration || 0) / 25));
    const group = new THREE.Group();

    // Core sphere
    const coreMat = new THREE.MeshPhongMaterial({
        color,
        emissive: color,
        emissiveIntensity: isHighlighted ? 0.9 : (isPlaying ? 1.0 : 0.15),
        shininess: 120,
    });
    group.add(new THREE.Mesh(new THREE.SphereGeometry(size, 14, 14), coreMat));

    // Highlighted: gold outer glow (additive blending = bloom-like effect)
    if (isHighlighted && !isPlaying) {
        const glowMat = new THREE.MeshBasicMaterial({
            color: '#ffcc00',
            transparent: true,
            opacity: 0.20,
            side: THREE.BackSide,
        });
        group.add(new THREE.Mesh(new THREE.SphereGeometry(size * 2.4, 14, 14), glowMat));
    }

    // Playing: white torus ring + bright inner glow
    if (isPlaying) {
        const ringMat = new THREE.MeshBasicMaterial({
            color: '#ffffff',
            transparent: true,
            opacity: 0.75,
        });
        group.add(new THREE.Mesh(new THREE.TorusGeometry(size * 1.7, 0.5, 8, 32), ringMat));
        // Secondary glow
        const pglow = new THREE.MeshBasicMaterial({
            color: '#ffffff',
            transparent: true,
            opacity: 0.12,
            side: THREE.BackSide,
        });
        group.add(new THREE.Mesh(new THREE.SphereGeometry(size * 2.8, 14, 14), pglow));
    }

    return group;
}

/* ─── NowPlaying bar (fixed bottom) ─────────────────────────────────────── */

function NowPlayingBar({ nowPlaying, onClose, onPrev, onNext }) {
    if (!nowPlaying) return null;
    const proc = PROCESS_META[nowPlaying.process_type] || {};
    const sg = SUBGROUP_META[nowPlaying.subgroup] || {};

    const navBtnStyle = {
        background: 'none', border: '1px solid var(--border)',
        borderRadius: 'var(--radius-full)', width: 28, height: 28, minWidth: 28,
        cursor: 'pointer', color: 'var(--text-secondary)', fontSize: '0.8rem',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'all 0.15s',
    };

    return createPortal(
        <div style={{
            position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 9999,
            background: 'rgba(255,255,255,0.97)',
            borderTop: '2px solid var(--border-bright)',
            backdropFilter: 'blur(12px)',
            boxShadow: '0 -4px 24px rgba(0,0,0,0.12)',
            padding: '0.75rem 1.5rem',
        }}>
            <div style={{ maxWidth: 1200, margin: '0 auto' }}>
                {/* Header row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
                    <span style={{ fontSize: '1.1rem' }}>{proc.icon || '🎵'}</span>
                    <span style={{ fontWeight: 700, fontSize: '0.9rem', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {nowPlaying.filename}
                    </span>
                    <span style={{
                        fontSize: '0.72rem', background: `${proc.color || '#e63946'}22`,
                        color: proc.color || '#e63946',
                        border: `1px solid ${proc.color || '#e63946'}44`,
                        borderRadius: 'var(--radius-full)', padding: '0.1rem 0.5rem', whiteSpace: 'nowrap',
                    }}>
                        {proc.label} · {sg.label}
                    </span>
                    {nowPlaying.duration > 0 && (
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                            {fmtDur(nowPlaying.duration)}
                        </span>
                    )}
                    {nowPlaying.loading && (
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }} className="loading-pulse">
                            Loading…
                        </span>
                    )}
                    <button onClick={onPrev} style={navBtnStyle} title="Previous file (↑)">▲</button>
                    <button onClick={onNext} style={navBtnStyle} title="Next file (↓)">▼</button>
                    <button
                        onClick={onClose}
                        style={{
                            background: 'none', border: 'none', cursor: 'pointer',
                            fontSize: '1.1rem', color: 'var(--text-muted)', lineHeight: 1,
                            padding: '0.2rem',
                        }}
                        title="Close player"
                    >
                        ✕
                    </button>
                </div>
                {nowPlaying.blob && (
                    <WaveformPlayer
                        label=""
                        audioBlob={nowPlaying.blob}
                        fileName={nowPlaying.filename}
                        color={proc.color || '#e63946'}
                        autoPlay={!!nowPlaying.autoPlay}
                    />
                )}
            </div>
        </div>,
        document.body
    );
}

/* ─── FileRow ────────────────────────────────────────────────────────────── */

function FileRow({ file, isPlaying, isLoading, onPlay, accentColor }) {
    return (
        <div
            data-file-id={file.id}
            style={{
                display: 'flex', alignItems: 'center', gap: '0.75rem',
                background: isPlaying ? `${accentColor}11` : 'var(--bg-surface)',
                border: `1px solid ${isPlaying ? accentColor + '44' : 'var(--border)'}`,
                borderRadius: 'var(--radius-md)', padding: '0.7rem 0.875rem',
                marginBottom: '0.5rem', transition: 'all 0.15s',
            }}
        >
            <button
                onClick={onPlay}
                disabled={isLoading}
                style={{
                    background: isLoading ? 'var(--bg-surface)' : (isPlaying ? accentColor : 'var(--bg-card)'),
                    border: `1.5px solid ${accentColor}`,
                    borderRadius: 'var(--radius-full)', width: 30, height: 30, minWidth: 30,
                    cursor: isLoading ? 'default' : 'pointer', color: isPlaying ? '#fff' : accentColor,
                    fontSize: '0.75rem', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.15s',
                }}
                title={isPlaying ? 'Now playing' : 'Play'}
            >
                {isLoading ? '⏳' : (isPlaying ? '♫' : '▶')}
            </button>

            <span style={{ flex: 1, fontWeight: 500, fontSize: '0.875rem', wordBreak: 'break-all', minWidth: 0 }}>
                {file.filename}
            </span>

            <div style={{ display: 'flex', gap: '0.4rem', flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                {file.duration > 0 && (
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                        {fmtDur(file.duration)}
                    </span>
                )}
                {file.file_size > 0 && (
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                        {fmtSize(file.file_size)}
                    </span>
                )}
                {file.created_at && (
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                        {fmtDate(file.created_at)}
                    </span>
                )}
            </div>
        </div>
    );
}

/* ─── Saved Files view ──────────────────────────────────────────────────── */

function FilesView({ files, loading, error, nowPlaying, loadingId, onPlay, onRefresh }) {
    const grouped = useMemo(() => {
        const g = {};
        for (const f of files) {
            const pt = f.process_type || 'other';
            const sg = f.subgroup || 'other';
            if (!g[pt]) g[pt] = {};
            if (!g[pt][sg]) g[pt][sg] = [];
            g[pt][sg].push(f);
        }
        return g;
    }, [files]);

    const processTypes = useMemo(() =>
        Object.keys(grouped).sort((a, b) => {
            const la = Math.max(...Object.values(grouped[a]).flat().map(f => +new Date(f.created_at || 0)));
            const lb = Math.max(...Object.values(grouped[b]).flat().map(f => +new Date(f.created_at || 0)));
            return lb - la;
        }),
        [grouped]
    );

    if (loading && files.length === 0) {
        return (
            <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-secondary)' }} className="loading-pulse">
                Loading your files…
            </div>
        );
    }

    if (!loading && files.length === 0 && !error) {
        return (
            <div style={{ textAlign: 'center', padding: '3rem 2rem' }}>
                <div style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>🎵</div>
                <h3 style={{ marginBottom: '0.5rem' }}>Your library is empty</h3>
                <p style={{ color: 'var(--text-secondary)', maxWidth: 400, margin: '0 auto' }}>
                    Every time you use a tool (Stem Separator, SyncTag, etc.),
                    the output files are automatically saved here.
                </p>
            </div>
        );
    }

    return (
        <div>
            {processTypes.map((pt) => {
                const procMeta = PROCESS_META[pt] || { label: pt, icon: '🎵', color: '#888' };
                const subOrder = PROCESS_SUBGROUP_ORDER[pt] || Object.keys(grouped[pt]);
                const validSubs = subOrder.filter(sg => grouped[pt][sg]?.length > 0);
                const total = Object.values(grouped[pt]).flat().length;

                return (
                    <div key={pt} className="card fade-in" style={{ marginBottom: '1.5rem' }}>
                        {/* Process header */}
                        <div style={{
                            display: 'flex', alignItems: 'center', gap: '0.6rem',
                            marginBottom: '1.25rem', paddingBottom: '0.75rem',
                            borderBottom: `2px solid ${procMeta.color}33`,
                        }}>
                            <span style={{ fontSize: '1.3rem' }}>{procMeta.icon}</span>
                            <h3 style={{ margin: 0, color: procMeta.color, fontSize: '1rem' }}>{procMeta.label}</h3>
                            <span style={{
                                marginLeft: 'auto', fontSize: '0.75rem', color: 'var(--text-muted)',
                                background: 'var(--bg-surface)', border: '1px solid var(--border)',
                                borderRadius: 'var(--radius-full)', padding: '0.12rem 0.5rem',
                            }}>
                                {total} file{total !== 1 ? 's' : ''}
                            </span>
                        </div>

                        {validSubs.map(sg => {
                            const sgMeta = SUBGROUP_META[sg] || { label: sg, color: procMeta.color };
                            const subFiles = grouped[pt][sg];
                            return (
                                <div key={sg} style={{ marginBottom: validSubs.length > 1 ? '1.25rem' : 0 }}>
                                    {validSubs.length > 1 && (
                                        <div style={{
                                            fontSize: '0.78rem', fontWeight: 600, color: sgMeta.color,
                                            textTransform: 'uppercase', letterSpacing: '0.05em',
                                            marginBottom: '0.5rem', paddingLeft: '0.25rem',
                                        }}>
                                            {sgMeta.label}
                                        </div>
                                    )}
                                    {subFiles.map(f => (
                                        <FileRow
                                            key={f.id}
                                            file={f}
                                            isPlaying={nowPlaying?.id === f.id}
                                            isLoading={loadingId === f.id}
                                            accentColor={sgMeta.color}
                                            onPlay={() => onPlay(f, { autoPlay: true })}
                                        />
                                    ))}
                                </div>
                            );
                        })}
                    </div>
                );
            })}
        </div>
    );
}

/* ─── SearchPanel (inside Smart Graph view) ─────────────────────────────── */

function SearchPanel({ token, results, hasMore, searchLoading, onSearch, onLoadMore }) {
    const [mode, setMode] = useState('audio'); // 'audio' | 'text'
    const [queryFile, setQueryFile] = useState(null);
    const [queryText, setQueryText] = useState('');
    const [dragover, setDragover] = useState(false);
    const fileRef = useRef(null);

    const canSearch = mode === 'audio' ? !!queryFile : !!queryText.trim();

    const handleSearch = () => {
        onSearch({ mode, file: queryFile, text: queryText, offset: 0 });
    };

    const handleLoadMore = () => {
        onLoadMore({ mode, file: queryFile, text: queryText, offset: results.length });
    };

    return (
        <div style={{
            width: 300, minWidth: 280, flexShrink: 0,
            display: 'flex', flexDirection: 'column', gap: '0.875rem',
            padding: '1rem', background: 'rgba(255,255,255,0.96)',
            borderRight: '1px solid var(--border)',
            overflowY: 'auto',
        }}>
            <div>
                <h4 style={{ margin: '0 0 0.5rem', fontSize: '0.9rem' }}>🔍 Similarity Search</h4>
                <p style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', margin: 0 }}>
                    Find your most similar stored files using M2D-CLAP embeddings.
                </p>
            </div>

            {/* Mode toggle */}
            <div style={{ display: 'flex', gap: '0.375rem' }}>
                {[['audio', '🎵 Audio'], ['text', '✏️ Text']].map(([m, label]) => (
                    <button
                        key={m}
                        onClick={() => setMode(m)}
                        className={`btn ${mode === m ? 'btn-primary' : 'btn-secondary'}`}
                        style={{ flex: 1, padding: '0.35rem 0', fontSize: '0.78rem', justifyContent: 'center' }}
                    >
                        {label}
                    </button>
                ))}
            </div>

            {/* Input */}
            {mode === 'audio' ? (
                <div
                    className={`upload-zone ${dragover ? 'dragover' : ''}`}
                    style={{ minHeight: 80, padding: '0.75rem', cursor: 'pointer' }}
                    onClick={() => fileRef.current?.click()}
                    onDragOver={e => { e.preventDefault(); setDragover(true); }}
                    onDragLeave={() => setDragover(false)}
                    onDrop={e => { e.preventDefault(); setDragover(false); if (e.dataTransfer.files[0]) setQueryFile(e.dataTransfer.files[0]); }}
                >
                    <span className="icon" style={{ fontSize: '1.4rem' }}>🎵</span>
                    <span className="label" style={{ fontSize: '0.78rem' }}>Drop audio or click</span>
                    {queryFile && <span className="file-name" style={{ fontSize: '0.72rem' }}>{queryFile.name}</span>}
                    <input ref={fileRef} type="file" hidden accept=".wav,.flac,.mp3,.aac,.aif,.aiff"
                        onChange={e => e.target.files[0] && setQueryFile(e.target.files[0])} />
                </div>
            ) : (
                <div>
                    <textarea
                        style={{
                            width: '100%', minHeight: 72, resize: 'vertical',
                            background: 'var(--bg-surface)', border: '1.5px solid var(--border)',
                            borderRadius: 'var(--radius-sm)', padding: '0.6rem 0.75rem',
                            fontSize: '0.85rem', color: 'var(--text-primary)',
                            fontFamily: 'inherit', outline: 'none', boxSizing: 'border-box',
                        }}
                        placeholder='e.g. "dark trap drums" or "uplifting piano melody"'
                        value={queryText}
                        onChange={e => setQueryText(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (canSearch) handleSearch(); } }}
                    />
                </div>
            )}

            <button
                className="btn btn-primary"
                style={{ width: '100%', justifyContent: 'center', padding: '0.45rem' }}
                disabled={!canSearch || searchLoading}
                onClick={handleSearch}
            >
                {searchLoading ? '⏳ Searching…' : '🔍 Search'}
            </button>

            {/* Results */}
            {results.length > 0 && (
                <div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
                        {results.length} result{results.length !== 1 ? 's' : ''} — highlighted on graph
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                        {results.map((r, i) => {
                            const proc = PROCESS_META[r.process_type] || {};
                            return (
                                <div key={r.id} style={{
                                    background: 'var(--bg-surface)',
                                    border: `1px solid ${proc.color || '#888'}44`,
                                    borderRadius: 'var(--radius-sm)', padding: '0.55rem 0.65rem',
                                    borderLeft: `3px solid ${proc.color || '#888'}`,
                                }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                                        <span style={{ fontSize: '0.9rem' }}>{proc.icon || '🎵'}</span>
                                        <span style={{ flex: 1, fontSize: '0.78rem', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {r.filename}
                                        </span>
                                        <span style={{
                                            fontSize: '0.7rem', fontWeight: 700,
                                            color: proc.color || '#888', whiteSpace: 'nowrap',
                                        }}>
                                            {(r.score * 100).toFixed(0)}%
                                        </span>
                                    </div>
                                    <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                                        {proc.label}{r.duration > 0 ? ` · ${fmtDur(r.duration)}` : ''}
                                    </div>
                                </div>
                            );
                        })}
                    </div>

                    {hasMore && (
                        <button
                            className="btn btn-secondary"
                            style={{ width: '100%', marginTop: '0.6rem', fontSize: '0.8rem', padding: '0.4rem' }}
                            disabled={searchLoading}
                            onClick={handleLoadMore}
                        >
                            {searchLoading ? '⏳' : '+ 10 More'}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

/* ─── SmartGraph view (premium) ─────────────────────────────────────────── */

function SmartGraph({ token, nowPlaying, loadingId, onPlay, graphRefreshKey }) {
    const [graphData, setGraphData] = useState(null);
    const [graphLoading, setGraphLoading] = useState(true);
    const [graphError, setGraphError] = useState('');

    // Search state
    const [allResults, setAllResults] = useState([]);
    const [hasMore, setHasMore] = useState(false);
    const [searchLoading, setSearchLoading] = useState(false);

    // Highlighted node IDs (accumulates across +5 more fetches)
    const highlightedIds = useMemo(() => new Set(allResults.map(r => r.id)), [allResults]);

    // Refs for nodeThreeObject closure (avoids stale captures)
    const highlightedIdsRef = useRef(highlightedIds);
    const nowPlayingIdRef = useRef(nowPlaying?.id || null);
    const graphRef = useRef(null);

    // Keep refs in sync and refresh the graph on changes
    useEffect(() => {
        highlightedIdsRef.current = highlightedIds;
        nowPlayingIdRef.current = nowPlaying?.id || null;
        graphRef.current?.refresh();
    }, [highlightedIds, nowPlaying?.id]);

    // Fetch graph data on mount and whenever graphRefreshKey increments
    useEffect(() => {
        if (!token) return;
        (async () => {
            setGraphLoading(true);
            setGraphError('');
            try {
                const resp = await fetch(`${API_BASE}/files/graph`, {
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
                const data = await resp.json();
                setGraphData(data);
            } catch (e) {
                setGraphError(e.message);
            } finally {
                setGraphLoading(false);
            }
        })();
    }, [token, graphRefreshKey]);

    // nodeThreeObject — reads from refs, never becomes stale
    const nodeThreeObject = useCallback((node) => {
        return makeNodeObj(
            node,
            highlightedIdsRef.current.has(node.id),
            nowPlayingIdRef.current === node.id,
        );
    }, []);

    const handleNodeClick = useCallback((node) => {
        onPlay(node);
    }, [onPlay]);

    const handleSearch = useCallback(async ({ mode, file, text, offset }) => {
        if (!token) return;
        setSearchLoading(true);
        try {
            const form = new FormData();
            if (mode === 'audio' && file) form.append('audio', file);
            else form.append('query_text', text);
            form.append('offset', String(offset));
            form.append('limit', '10');

            const resp = await fetch(`${API_BASE}/files/search`, {
                method: 'POST',
                body: form,
                headers: { Authorization: `Bearer ${token}` },
            });
            if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);
            const data = await resp.json();

            if (offset === 0) {
                // New search: replace results
                setAllResults(data.results || []);
            } else {
                // Paginate: append (de-duplicate by id)
                setAllResults(prev => {
                    const seen = new Set(prev.map(r => r.id));
                    const fresh = (data.results || []).filter(r => !seen.has(r.id));
                    return [...prev, ...fresh];
                });
            }
            setHasMore(data.has_more || false);
        } catch (e) {
            console.error('Search error:', e);
        } finally {
            setSearchLoading(false);
        }
    }, [token]);

    const handleLoadMore = useCallback((params) => handleSearch(params), [handleSearch]);

    // Link color / particle helpers
    const linkColorFn = useCallback((link) => linkColor(link.similarity || 0), []);
    const linkWidthFn = useCallback((link) => {
        const s = link.similarity || 0;
        return s >= 0.8 ? 1.2 : s >= 0.65 ? 0.7 : 0.3;
    }, []);
    const linkParticles = useCallback((link) => {
        const src = typeof link.source === 'object' ? link.source.id : link.source;
        const tgt = typeof link.target === 'object' ? link.target.id : link.target;
        return (highlightedIdsRef.current.has(src) && highlightedIdsRef.current.has(tgt)) ? 5 : 0;
    }, []);

    return (
        <div style={{ display: 'flex', flex: 1, overflow: 'hidden', height: '100%' }}>
            {/* Left: search panel */}
            <SearchPanel
                token={token}
                results={allResults}
                hasMore={hasMore}
                searchLoading={searchLoading}
                onSearch={handleSearch}
                onLoadMore={handleLoadMore}
            />

            {/* Right: 3D graph */}
            <div style={{ flex: 1, background: '#0a0a18', position: 'relative', overflow: 'hidden' }}>
                {graphLoading && (
                    <div style={{
                        position: 'absolute', inset: 0, display: 'flex',
                        alignItems: 'center', justifyContent: 'center',
                        color: '#8888cc', fontSize: '0.9rem',
                    }} className="loading-pulse">
                        Computing similarity graph…
                    </div>
                )}

                {graphError && (
                    <div style={{
                        position: 'absolute', inset: 0, display: 'flex',
                        alignItems: 'center', justifyContent: 'center',
                        color: '#ff6b6b', fontSize: '0.85rem', padding: '2rem', textAlign: 'center',
                    }}>
                        ❌ {graphError}
                    </div>
                )}

                {!graphLoading && !graphError && graphData?.nodes?.length === 0 && (
                    <div style={{
                        position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
                        alignItems: 'center', justifyContent: 'center', color: '#8888cc',
                    }}>
                        <div style={{ fontSize: '2.5rem', marginBottom: '0.75rem' }}>🕸️</div>
                        <p style={{ fontSize: '0.9rem', maxWidth: 280, textAlign: 'center' }}>
                            No files in your graph yet. Use any tool to start building your library.
                        </p>
                    </div>
                )}

                {!graphLoading && !graphError && graphData?.nodes?.length > 0 && (
                    <Suspense fallback={
                        <div style={{ color: '#8888cc', padding: '2rem', textAlign: 'center' }} className="loading-pulse">
                            Loading 3D renderer…
                        </div>
                    }>
                        <ForceGraph3D
                            ref={graphRef}
                            graphData={graphData}
                            backgroundColor="#0a0a18"
                            nodeLabel={n => `${n.filename}\n${(PROCESS_META[n.process_type] || {}).label || n.process_type}`}
                            nodeThreeObject={nodeThreeObject}
                            nodeThreeObjectExtend={false}
                            linkColor={linkColorFn}
                            linkWidth={linkWidthFn}
                            linkOpacity={1}
                            linkDirectionalParticles={linkParticles}
                            linkDirectionalParticleWidth={1.5}
                            linkDirectionalParticleSpeed={0.006}
                            onNodeClick={handleNodeClick}
                            cooldownTicks={180}
                            onEngineStop={() => graphRef.current?.zoomToFit(600, 60)}
                            enableNodeDrag={true}
                            enableNavigationControls={true}
                        />
                    </Suspense>
                )}

                {/* Legend */}
                {!graphLoading && !graphError && graphData?.nodes?.length > 0 && (
                    <div style={{
                        position: 'absolute', bottom: 12, right: 12,
                        background: 'rgba(10,10,24,0.88)', border: '1px solid rgba(255,255,255,0.1)',
                        borderRadius: 8, padding: '0.6rem 0.75rem', display: 'flex', flexDirection: 'column', gap: '0.3rem',
                    }}>
                        {Object.entries(PROCESS_META).map(([key, meta]) => (
                            graphData.nodes.some(n => n.process_type === key) ? (
                                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.7rem' }}>
                                    <div style={{ width: 8, height: 8, borderRadius: '50%', background: meta.color, flexShrink: 0 }} />
                                    <span style={{ color: '#aaa' }}>{meta.label}</span>
                                </div>
                            ) : null
                        ))}
                        <div style={{ borderTop: '1px solid rgba(255,255,255,0.1)', marginTop: '0.25rem', paddingTop: '0.25rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.68rem' }}>
                                <div style={{ width: 16, height: 2, background: 'rgba(0,255,136,0.7)', flexShrink: 0 }} />
                                <span style={{ color: '#888' }}>High similarity</span>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.68rem' }}>
                                <div style={{ width: 16, height: 2, background: 'rgba(0,200,255,0.5)', flexShrink: 0 }} />
                                <span style={{ color: '#888' }}>Medium</span>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.68rem' }}>
                                <div style={{ width: 16, height: 2, background: 'rgba(140,80,255,0.35)', flexShrink: 0 }} />
                                <span style={{ color: '#888' }}>Low</span>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

/* ─── Upgrade wall (signed-in but no subscription) ──────────────────────── */

const PLANS = [
    {
        id: 'standard',
        name: 'Standard',
        price: '$9.99 / mo',
        desc: 'Unlimited use of all processing tools — no daily limits.',
        color: '#7c3aed',
    },
    {
        id: 'premium',
        name: 'Premium',
        price: '$14.99 / mo',
        desc: 'Everything in Standard + file storage, smart graph, and AI similarity search.',
        color: '#d97706',
        badge: 'Recommended',
    },
];

function UpgradeWall({ onSelectPlan }) {
    return (
        <div className="fade-in">
            <div className="card" style={{ textAlign: 'center', padding: '2.5rem 2rem 2rem' }}>
                <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>🗂️</div>
                <h2 style={{ marginBottom: '0.5rem' }}>My Files requires a subscription</h2>
                <p style={{ color: 'var(--text-secondary)', maxWidth: 480, margin: '0 auto 2rem', fontSize: '0.95rem' }}>
                    Every file you process is automatically saved and embedded into your personal audio graph.
                    Upgrade to unlock access to your library and smart similarity search.
                </p>

                <div style={{ display: 'flex', gap: '1.25rem', justifyContent: 'center', flexWrap: 'wrap' }}>
                    {PLANS.map((plan) => (
                        <div
                            key={plan.id}
                            style={{
                                flex: '1 1 220px', maxWidth: 280,
                                border: `2px solid ${plan.color}`,
                                borderRadius: 14, padding: '1.5rem',
                                textAlign: 'left', position: 'relative',
                            }}
                        >
                            {plan.badge && (
                                <span style={{
                                    position: 'absolute', top: -12, left: '50%', transform: 'translateX(-50%)',
                                    background: plan.color, color: '#fff', fontSize: '0.72rem', fontWeight: 700,
                                    borderRadius: 'var(--radius-full)', padding: '0.2rem 0.75rem',
                                    whiteSpace: 'nowrap',
                                }}>
                                    {plan.badge}
                                </span>
                            )}
                            <h3 style={{ color: plan.color, margin: '0 0 0.25rem', fontSize: '1.1rem' }}>{plan.name}</h3>
                            <p style={{ fontSize: '1.5rem', fontWeight: 700, margin: '0 0 0.75rem' }}>{plan.price}</p>
                            <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', margin: '0 0 1.25rem', lineHeight: 1.5 }}>
                                {plan.desc}
                            </p>
                            <button
                                className="btn btn-primary"
                                style={{
                                    width: '100%', justifyContent: 'center',
                                    background: plan.color, borderColor: plan.color,
                                }}
                                onClick={() => onSelectPlan(plan.id)}
                            >
                                Choose {plan.name}
                            </button>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}

/* ─── Main component ────────────────────────────────────────────────────── */

export default function MyFilesTab() {
    const { getToken, isSignedIn } = useAuth();
    const { profile, openSubModal, setSubModalOpen } = useAuthContext();
    const isPremium = !!profile?.subscription_active;

    const [view, setView] = useState('files');        // 'files' | 'graph'
    const [files, setFiles] = useState([]);
    const [filesLoading, setFilesLoading] = useState(false);
    const [filesError, setFilesError] = useState('');
    const [token, setToken] = useState(null);

    // Bulk upload state
    const [bulkUploading, setBulkUploading] = useState(false);
    const [bulkStatus, setBulkStatus] = useState('');
    const bulkInputRef = useRef(null);

    // NowPlaying state
    const [nowPlaying, setNowPlaying] = useState(null); // {id, filename, process_type, subgroup, duration, blob, loading}
    const [loadingId, setLoadingId] = useState(null);

    // Fetch files + token on mount / sign-in
    const fetchFiles = useCallback(async () => {
        if (!isSignedIn) return;
        setFilesLoading(true);
        setFilesError('');
        try {
            const t = await getToken();
            setToken(t);
            const resp = await fetch(`${API_BASE}/files`, {
                headers: { Authorization: `Bearer ${t}` },
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            setFiles(data.files || []);
        } catch (e) {
            setFilesError(e.message);
        } finally {
            setFilesLoading(false);
        }
    }, [isSignedIn, getToken]);

    useEffect(() => { fetchFiles(); }, [fetchFiles]);

    // ── Background-embedding poller ────────────────────────────────────────
    // Processing tabs fire window.dispatchEvent(new CustomEvent('audioProcessed'))
    // and write localStorage.lastProcessedAt so this tab knows to start polling
    // until all background embeddings are committed to Qdrant.

    const filesLengthRef = useRef(0);
    const pollingTimerRef = useRef(null);
    const stableCountRef = useRef(0);
    const [graphRefreshKey, setGraphRefreshKey] = useState(0);
    const [isPolling, setIsPolling] = useState(false);

    // Track file count changes: bump graphRefreshKey when new files arrive;
    // stop polling when count is stable for 3 consecutive 4-second polls.
    useEffect(() => {
        const prev = filesLengthRef.current;
        const next = files.length;
        if (next > prev) {
            // New files arrived — trigger graph refresh if polling is active
            if (pollingTimerRef.current !== null) {
                setGraphRefreshKey(k => k + 1);
            }
            stableCountRef.current = 0;
        } else if (pollingTimerRef.current !== null) {
            stableCountRef.current += 1;
            if (stableCountRef.current >= 3) {
                clearInterval(pollingTimerRef.current);
                pollingTimerRef.current = null;
                setIsPolling(false);
            }
        }
        filesLengthRef.current = next;
    }, [files]); // eslint-disable-line react-hooks/exhaustive-deps

    const startPolling = useCallback(() => {
        if (pollingTimerRef.current) return; // already running
        stableCountRef.current = 0;
        setIsPolling(true);
        pollingTimerRef.current = setInterval(() => { fetchFiles(); }, 4000);
        // Safety cap: stop after 5 minutes regardless
        setTimeout(() => {
            if (pollingTimerRef.current) {
                clearInterval(pollingTimerRef.current);
                pollingTimerRef.current = null;
                setIsPolling(false);
            }
        }, 300_000);
    }, [fetchFiles]);

    // Start polling when a processing tab signals completion, or if this tab
    // mounts within 5 minutes of the last processing event (tab-switch case).
    useEffect(() => {
        const handler = () => { if (isSignedIn) startPolling(); };
        window.addEventListener('audioProcessed', handler);

        const lastProcessed = Number(localStorage.getItem('lastProcessedAt') || 0);
        if (isSignedIn && Date.now() - lastProcessed < 300_000) {
            startPolling();
        }

        return () => window.removeEventListener('audioProcessed', handler);
    }, [startPolling, isSignedIn]);

    // Cleanup timer if the component unmounts
    useEffect(() => () => {
        if (pollingTimerRef.current) clearInterval(pollingTimerRef.current);
    }, []);

    // Load audio for a file (from either list or graph)
    const handlePlay = useCallback(async (file, { autoPlay = false } = {}) => {
        // If clicking the already-playing file, just surface the bar (don't re-fetch)
        if (nowPlaying?.id === file.id && !nowPlaying?.loading) return;

        setLoadingId(file.id);
        setNowPlaying({
            id: file.id,
            filename: file.filename,
            process_type: file.process_type,
            subgroup: file.subgroup,
            duration: file.duration,
            blob: null,
            loading: true,
            autoPlay,
        });

        try {
            const t = token || await getToken();
            const resp = await fetch(`${API_BASE}/files/${file.id}/audio`, {
                headers: { Authorization: `Bearer ${t}` },
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const blob = await resp.blob();
            setNowPlaying(prev =>
                prev?.id === file.id ? { ...prev, blob, loading: false } : prev
            );
        } catch (e) {
            console.error('Audio load error:', e);
            setNowPlaying(prev =>
                prev?.id === file.id ? { ...prev, loading: false } : prev
            );
        } finally {
            setLoadingId(null);
        }
    }, [nowPlaying, token, getToken]);

    // ── Flat ordered file list (matches FilesView render order) ───────────
    const orderedFiles = useMemo(() => {
        const g = {};
        for (const f of files) {
            const pt = f.process_type || 'other';
            const sg = f.subgroup || 'other';
            if (!g[pt]) g[pt] = {};
            if (!g[pt][sg]) g[pt][sg] = [];
            g[pt][sg].push(f);
        }
        const processTypes = Object.keys(g).sort((a, b) => {
            const la = Math.max(...Object.values(g[a]).flat().map(f => +new Date(f.created_at || 0)));
            const lb = Math.max(...Object.values(g[b]).flat().map(f => +new Date(f.created_at || 0)));
            return lb - la;
        });
        const flat = [];
        for (const pt of processTypes) {
            const subOrder = PROCESS_SUBGROUP_ORDER[pt] || Object.keys(g[pt]);
            for (const sg of subOrder) {
                if (g[pt]?.[sg]) flat.push(...g[pt][sg]);
            }
        }
        return flat;
    }, [files]);

    // ── Navigate prev/next in the file list ───────────────────────────────
    const navigateFiles = useCallback((dir) => {
        if (orderedFiles.length === 0) return;
        // Stop every playing WaveformPlayer and native <audio> immediately.
        window.dispatchEvent(new CustomEvent('waveform:stop-all'));
        const idx = orderedFiles.findIndex(f => f.id === nowPlaying?.id);
        const next = dir > 0
            ? orderedFiles[(idx + 1) % orderedFiles.length]
            : orderedFiles[(idx - 1 + orderedFiles.length) % orderedFiles.length];
        handlePlay(next, { autoPlay: true });
        requestAnimationFrame(() => {
            document.querySelector(`[data-file-id="${next.id}"]`)
                ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
    }, [orderedFiles, nowPlaying?.id, handlePlay]);

    // ── Arrow-key navigation (only when Files sub-tab is visible) ─────────
    useEffect(() => {
        if (view !== 'files') return;
        const onKey = (e) => {
            if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
            const tag = document.activeElement?.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || document.activeElement?.isContentEditable) return;
            e.preventDefault();
            navigateFiles(e.key === 'ArrowDown' ? 1 : -1);
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [view, navigateFiles]);

    // ── Bulk upload handler ────────────────────────────────────────────────
    const handleBulkUpload = useCallback(async (e) => {
        const selectedFiles = Array.from(e.target.files || []);
        if (selectedFiles.length === 0) return;
        // Reset the input so the same selection can be re-triggered
        e.target.value = '';

        setBulkUploading(true);
        setBulkStatus(`Uploading ${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''}…`);
        try {
            const t = token || await getToken();
            const form = new FormData();
            selectedFiles.forEach(f => form.append('audio', f));

            const resp = await fetch(`${API_BASE}/files/upload`, {
                method: 'POST',
                body: form,
                headers: { Authorization: `Bearer ${t}` },
            });
            if (!resp.ok) {
                const text = await resp.text();
                throw new Error(`API ${resp.status}: ${text}`);
            }
            const data = await resp.json();
            setBulkStatus(`✅ ${data.queued} file${data.queued !== 1 ? 's' : ''} queued for embedding`);
            // Refresh file list + start polling for new embeddings
            fetchFiles();
            startPolling();
            localStorage.setItem('lastProcessedAt', String(Date.now()));
        } catch (err) {
            setBulkStatus(`❌ ${err.message}`);
        } finally {
            setBulkUploading(false);
            // Clear status after 6 seconds
            setTimeout(() => setBulkStatus(''), 6000);
        }
    }, [token, getToken, fetchFiles, startPolling]);

    if (!isSignedIn) {
        return (
            <div className="fade-in">
                <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
                    <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>🔒</div>
                    <h2 style={{ marginBottom: '0.5rem' }}>Sign In to View Your Files</h2>
                    <p style={{ color: 'var(--text-secondary)' }}>
                        Your processed audio files will appear here once you sign in and use any tool.
                    </p>
                </div>
            </div>
        );
    }

    // Profile still loading — brief spinner before gating
    if (isSignedIn && profile === null) {
        return (
            <div className="fade-in">
                <div className="card loading-pulse" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-secondary)' }}>
                    Loading your account…
                </div>
            </div>
        );
    }

    // Free tier — show upgrade wall
    if (isSignedIn && profile && !profile.subscription_active) {
        return <UpgradeWall onSelectPlan={(planId) => openSubModal(planId)} />;
    }

    const graphHeight = 'calc(100vh - var(--topbar-h) - 3rem)';

    return (
        <div className="fade-in" style={{ paddingBottom: nowPlaying ? 170 : 0 }}>
            {/* ── Header + sub-tab bar ──────────────────────────── */}
            <div className="card" style={{ marginBottom: '1.5rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
                    <div style={{ flex: 1 }}>
                        <h2 style={{ margin: 0, fontSize: '1.3rem' }}>My Files</h2>
                        <p style={{ color: 'var(--text-secondary)', margin: '0.2rem 0 0', fontSize: '0.85rem' }}>
                            {files.length === 0 && !filesLoading
                                ? 'No files yet — use any tool to start building your library.'
                                : `${files.length} file${files.length !== 1 ? 's' : ''} stored`}
                            {isPolling && (
                                <span className="loading-pulse" style={{ marginLeft: '0.5rem', color: 'var(--text-muted)' }}>
                                    · syncing…
                                </span>
                            )}
                        </p>
                    </div>

                    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                        {/* Saved Files sub-tab */}
                        <button
                            className={`btn ${view === 'files' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ padding: '0.4rem 1rem', fontSize: '0.82rem' }}
                            onClick={() => setView('files')}
                        >
                            📂 Saved Files
                        </button>

                        {/* Smart Graph sub-tab */}
                        <button
                            className={`btn ${view === 'graph' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ padding: '0.4rem 1rem', fontSize: '0.82rem', position: 'relative' }}
                            onClick={() => {
                                if (!isPremium) { openSubModal('premium'); return; }
                                setView('graph');
                            }}
                        >
                            🕸️ Smart Graph
                            {!isPremium && (
                                <span style={{
                                    marginLeft: '0.35rem', fontSize: '0.62rem',
                                    background: '#f59e0b', color: '#fff',
                                    borderRadius: 'var(--radius-full)', padding: '0.05rem 0.35rem', fontWeight: 700,
                                }}>PRO</span>
                            )}
                        </button>

                        {/* Bulk Upload to Graph */}
                        <button
                            className="btn btn-secondary"
                            style={{ padding: '0.4rem 1rem', fontSize: '0.82rem', position: 'relative' }}
                            disabled={bulkUploading}
                            onClick={() => {
                                if (!isPremium) { openSubModal('premium'); return; }
                                bulkInputRef.current?.click();
                            }}
                        >
                            {bulkUploading ? '⏳ Uploading…' : '📤 Upload to Graph'}
                            {!isPremium && (
                                <span style={{
                                    marginLeft: '0.35rem', fontSize: '0.62rem',
                                    background: '#f59e0b', color: '#fff',
                                    borderRadius: 'var(--radius-full)', padding: '0.05rem 0.35rem', fontWeight: 700,
                                }}>PRO</span>
                            )}
                        </button>
                        <input
                            ref={bulkInputRef}
                            type="file"
                            multiple
                            hidden
                            accept=".wav,.flac,.mp3,.aac,.aif,.aiff,.ogg"
                            onChange={handleBulkUpload}
                        />

                        {view === 'files' && (
                            <button
                                className="btn btn-secondary"
                                style={{ padding: '0.4rem 0.65rem', fontSize: '0.82rem' }}
                                onClick={fetchFiles} disabled={filesLoading} title="Refresh"
                            >
                                {filesLoading ? '⏳' : '↻'}
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {filesError && (
                <div className="card" style={{ marginBottom: '1.5rem' }}>
                    <p className="status-error">❌ {filesError}</p>
                </div>
            )}

            {bulkStatus && (
                <div className="card" style={{ marginBottom: '1.5rem' }}>
                    <p style={{
                        margin: 0, fontSize: '0.9rem', fontWeight: 500,
                        color: bulkStatus.startsWith('❌') ? 'var(--danger)' : 'var(--text-secondary)',
                    }}>{bulkStatus}</p>
                </div>
            )}

            {/* ── Saved Files view ─────────────────────────────────── */}
            {view === 'files' && (
                <FilesView
                    files={files}
                    loading={filesLoading}
                    error={filesError}
                    nowPlaying={nowPlaying}
                    loadingId={loadingId}
                    onPlay={handlePlay}
                    onRefresh={fetchFiles}
                />
            )}

            {/* ── Smart Graph view ─────────────────────────────────── */}
            {view === 'graph' && isPremium && token && (
                <div style={{
                    height: graphHeight, display: 'flex', flexDirection: 'column',
                    border: '1px solid var(--border)', borderRadius: 'var(--radius-md)',
                    overflow: 'hidden', background: '#0a0a18',
                }}>
                    <SmartGraph
                        token={token}
                        nowPlaying={nowPlaying}
                        loadingId={loadingId}
                        onPlay={handlePlay}
                        graphRefreshKey={graphRefreshKey}
                    />
                </div>
            )}

            {/* ── NowPlaying bar (portalled to document.body to escape CSS transforms) */}
            <NowPlayingBar
                nowPlaying={nowPlaying}
                onClose={() => setNowPlaying(null)}
                onPrev={() => navigateFiles(-1)}
                onNext={() => navigateFiles(1)}
            />
        </div>
    );
}
