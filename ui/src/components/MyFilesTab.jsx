/**
 * MyFilesTab — Library of the user's stored audio files.
 *
 * Sub-tabs:
 *   "Saved Files" — grouped list with Upload button (standard + premium; upload is premium-only)
 *   "Smart Search" — AI similarity search using advanced AI embeddings (Premium only)
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from '@clerk/clerk-react';
import WaveformPlayer from './WaveformPlayer.jsx';
import { useAuthContext } from '../AuthContext.jsx';

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

function FileRow({ file, isPlaying, isLoading, onPlay, selected, onSelect, accentColor }) {
    return (
        <div
            data-file-id={file.id}
            style={{
                display: 'flex', alignItems: 'center', gap: '0.75rem',
                background: selected ? `${accentColor}18` : (isPlaying ? `${accentColor}11` : 'var(--bg-surface)'),
                border: `1px solid ${selected ? accentColor + '66' : (isPlaying ? accentColor + '44' : 'var(--border)')}`,
                borderRadius: 'var(--radius-md)', padding: '0.7rem 0.875rem',
                marginBottom: '0.5rem', transition: 'all 0.15s',
            }}
        >
            {/* Checkbox */}
            <input
                type="checkbox"
                checked={selected}
                onChange={onSelect}
                style={{ width: 15, height: 15, minWidth: 15, cursor: 'pointer', accentColor, flexShrink: 0 }}
                title="Select file"
            />

            {/* Play button */}
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

            {/* Filename */}
            <span style={{ flex: 1, fontWeight: 500, fontSize: '0.875rem', wordBreak: 'break-all', minWidth: 0 }}>
                {file.filename}
            </span>

            {/* Metadata */}
            <div style={{ display: 'flex', gap: '0.4rem', flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end', alignItems: 'center' }}>
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

/* ─── Shared: bulk selection bar ────────────────────────────────────────── */

function BulkSelectionBar({ count, onDownload, onRequestDelete, onClear }) {
    return (
        <div className="card" style={{
            marginBottom: '1rem',
            display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap',
            background: 'var(--bg-card)', borderColor: 'var(--border)',
        }}>
            <span style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', flex: 1 }}>
                {count} file{count !== 1 ? 's' : ''} selected
            </span>
            <button className="btn btn-secondary" style={{ padding: '0.35rem 0.9rem', fontSize: '0.82rem' }} onClick={onDownload}>
                ⬇ Download
            </button>
            <button
                className="btn"
                style={{ padding: '0.35rem 0.9rem', fontSize: '0.82rem', background: '#ef4444', color: '#fff', border: '1px solid #ef4444' }}
                onClick={onRequestDelete}
            >
                🗑 Delete
            </button>
            <button
                onClick={onClear}
                style={{
                    background: 'none', border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-full)', width: 24, height: 24, minWidth: 24,
                    cursor: 'pointer', color: 'var(--text-muted)', fontSize: '0.65rem',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 0,
                }}
                title="Clear selection"
            >
                ✕
            </button>
        </div>
    );
}

/* ─── Shared: delete confirmation overlay ───────────────────────────────── */

function DeleteConfirmOverlay({ count, onConfirm, onCancel, deleting }) {
    return createPortal(
        <div style={{
            position: 'fixed', inset: 0,
            background: 'rgba(0,0,0,0.6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 1000,
        }}>
            <div className="card" style={{ maxWidth: 380, width: '90%', textAlign: 'center', padding: '2rem' }}>
                <h3 style={{ margin: '0 0 0.5rem', fontSize: '1.1rem' }}>Are you sure?</h3>
                <p style={{ color: 'var(--text-secondary)', margin: '0 0 1.5rem', fontSize: '0.9rem' }}>
                    Deleting {count} file{count !== 1 ? 's' : ''} is permanent and cannot be undone.
                </p>
                <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
                    <button
                        className="btn btn-secondary"
                        style={{ padding: '0.5rem 1.25rem' }}
                        onClick={onCancel}
                        disabled={deleting}
                    >
                        No
                    </button>
                    <button
                        className="btn"
                        style={{ padding: '0.5rem 1.25rem', background: '#ef4444', color: '#fff', border: '1px solid #ef4444' }}
                        onClick={onConfirm}
                        disabled={deleting}
                    >
                        {deleting ? 'Deleting…' : 'Yes, delete'}
                    </button>
                </div>
            </div>
        </div>,
        document.body
    );
}

/* ─── Saved Files view ──────────────────────────────────────────────────── */

function FilesView({ files, loading, error, nowPlaying, loadingId, onPlay, onDelete, onDownloadFile, onUploadClick, bulkUploading, canUpload }) {
    const [selectedIds, setSelectedIds] = useState(new Set());
    const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
    const [deletingBulk, setDeletingBulk] = useState(false);

    const toggleSelect = useCallback((id) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }, []);

    const handleDownloadSelected = useCallback(async () => {
        const selected = files.filter(f => selectedIds.has(f.id));
        for (const f of selected) {
            await onDownloadFile(f);
        }
    }, [files, selectedIds, onDownloadFile]);

    const handleConfirmDelete = useCallback(async () => {
        setDeletingBulk(true);
        await Promise.allSettled(Array.from(selectedIds).map(id => onDelete(id)));
        setSelectedIds(new Set());
        setShowDeleteConfirm(false);
        setDeletingBulk(false);
    }, [selectedIds, onDelete]);

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

    return (
        <div>
            {/* Upload section */}
            <div className="card" style={{
                marginBottom: '1.5rem',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                gap: '1rem', flexWrap: 'wrap',
            }}>
                <p style={{ margin: 0, fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                    Upload audio files directly to your library for AI similarity search.
                </p>
                <button
                    className="btn btn-secondary"
                    style={{ padding: '0.4rem 1rem', fontSize: '0.82rem', position: 'relative', whiteSpace: 'nowrap' }}
                    disabled={bulkUploading}
                    onClick={() => {
                        if (!canUpload) {
                            onUploadClick(); // This will trigger the premium check in parent
                        } else {
                            onUploadClick();
                        }
                    }}
                >
                    {bulkUploading ? '⏳ Uploading…' : '📤 Upload'}
                    {!canUpload && (
                        <span style={{
                            marginLeft: '0.35rem', fontSize: '0.62rem',
                            background: '#f59e0b', color: '#fff',
                            borderRadius: 'var(--radius-full)', padding: '0.05rem 0.35rem', fontWeight: 700,
                        }}>PREMIUM</span>
                    )}
                </button>
            </div>

            {selectedIds.size > 0 && (
                <BulkSelectionBar
                    count={selectedIds.size}
                    onDownload={handleDownloadSelected}
                    onRequestDelete={() => setShowDeleteConfirm(true)}
                    onClear={() => setSelectedIds(new Set())}
                />
            )}

            {/* Empty state */}
            {!loading && files.length === 0 && !error && (
                <div style={{ textAlign: 'center', padding: '3rem 2rem' }}>
                    <div style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>🎵</div>
                    <h3 style={{ marginBottom: '0.5rem' }}>Your library is empty</h3>
                    <p style={{ color: 'var(--text-secondary)', maxWidth: 400, margin: '0 auto' }}>
                        Every time you use a tool (Stem Separator, SyncTag, etc.),
                        the output files are automatically saved here.
                    </p>
                </div>
            )}

            {/* File groups */}
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
                                            selected={selectedIds.has(f.id)}
                                            onSelect={() => toggleSelect(f.id)}
                                        />
                                    ))}
                                </div>
                            );
                        })}
                    </div>
                );
            })}

            {showDeleteConfirm && (
                <DeleteConfirmOverlay
                    count={selectedIds.size}
                    onConfirm={handleConfirmDelete}
                    onCancel={() => setShowDeleteConfirm(false)}
                    deleting={deletingBulk}
                />
            )}
        </div>
    );
}

/* ─── Smart Search (standalone, no 3D graph) ─────────────────────────────── */

function SmartSearch({ getToken, onDelete, onDownloadFile, isPremiumUser, onPremiumAction }) {
    const { setIsProcessing } = useAuthContext();
    const [queryFile, setQueryFile] = useState(null);
    const [results, setResults] = useState([]);
    const [hasMore, setHasMore] = useState(false);
    const [searching, setSearching] = useState(false);
    const [dragover, setDragover] = useState(false);
    const [selectedIds, setSelectedIds] = useState(new Set());
    const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
    const [deletingBulk, setDeletingBulk] = useState(false);
    const fileRef = useRef(null);

    useEffect(() => {
        setIsProcessing(searching || deletingBulk);
        return () => setIsProcessing(false);
    }, [searching, deletingBulk, setIsProcessing]);

    const search = useCallback(async (offset = 0) => {
        if (!queryFile) return;
        setSearching(true);
        try {
            const form = new FormData();
            form.append('audio', queryFile);
            form.append('offset', String(offset));
            form.append('limit', '10');

            const t = await getToken();
            const resp = await fetch(`${API_BASE}/files/search`, {
                method: 'POST',
                body: form,
                headers: { Authorization: `Bearer ${t}` },
            });
            if (!resp.ok) throw new Error(`API ${resp.status}: ${await resp.text()}`);
            const data = await resp.json();

            if (offset === 0) {
                setResults(data.results || []);
                setSelectedIds(new Set());
            } else {
                setResults(prev => {
                    const seen = new Set(prev.map(r => r.id));
                    return [...prev, ...(data.results || []).filter(r => !seen.has(r.id))];
                });
            }
            setHasMore(data.has_more || false);
        } catch (e) {
            console.error('[SmartSearch] error:', e);
        } finally {
            setSearching(false);
        }
    }, [getToken, queryFile]);

    const toggleSelect = useCallback((id) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }, []);

    const handleDownloadSelected = useCallback(async () => {
        for (const r of results.filter(r => selectedIds.has(r.id))) {
            await onDownloadFile(r);
        }
    }, [results, selectedIds, onDownloadFile]);

    const handleConfirmDelete = useCallback(async () => {
        setDeletingBulk(true);
        const ids = Array.from(selectedIds);
        await Promise.allSettled(ids.map(id => onDelete(id)));
        setResults(prev => prev.filter(r => !selectedIds.has(r.id)));
        setSelectedIds(new Set());
        setShowDeleteConfirm(false);
        setDeletingBulk(false);
    }, [selectedIds, onDelete]);

    return (
        <div className="card fade-in">
            <div style={{ maxWidth: 600, margin: '0 auto' }}>
                <h3 style={{ margin: '0 0 0.4rem' }}>🔍 Smart Search</h3>
                <p style={{ color: 'var(--text-secondary)', margin: '0 0 1.25rem', fontSize: '0.875rem' }}>
                    Find the most similar files in your library using advanced AI embeddings.
                </p>

                {/* Audio drop zone */}
                <div
                    className={`upload-zone ${dragover ? 'dragover' : ''}`}
                    style={{ marginBottom: '1rem', cursor: 'pointer' }}
                    onClick={() => fileRef.current?.click()}
                    onDragOver={e => { e.preventDefault(); setDragover(true); }}
                    onDragLeave={() => setDragover(false)}
                    onDrop={e => {
                        e.preventDefault(); setDragover(false);
                        if (e.dataTransfer.files[0]) setQueryFile(e.dataTransfer.files[0]);
                    }}
                >
                    <span className="icon" style={{ fontSize: '1.8rem' }}>🎵</span>
                    <span className="label">Drop audio reference or click to browse</span>
                    {queryFile && <span className="file-name">{queryFile.name}</span>}
                    <input
                        ref={fileRef} type="file" hidden
                        accept=".wav,.flac,.mp3,.aac"
                        onChange={e => e.target.files[0] && setQueryFile(e.target.files[0])}
                    />
                </div>

                <button
                    className="btn btn-primary"
                    style={{ width: '100%', justifyContent: 'center', marginBottom: '1.5rem' }}
                    disabled={searching}
                    onClick={() => {
                        if (!isPremiumUser) {
                            onPremiumAction('AI Smart Search');
                        } else if (queryFile) {
                            search(0);
                        }
                    }}
                >
                    {searching ? '⏳ Searching…' : '🔍 Find Similar'}
                </button>

                {/* Bulk action bar */}
                {selectedIds.size > 0 && (
                    <BulkSelectionBar
                        count={selectedIds.size}
                        onDownload={handleDownloadSelected}
                        onRequestDelete={() => setShowDeleteConfirm(true)}
                        onClear={() => setSelectedIds(new Set())}
                    />
                )}

                {/* Results */}
                {results.length > 0 && (
                    <div>
                        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '0.75rem', fontWeight: 600 }}>
                            {results.length} result{results.length !== 1 ? 's' : ''} found
                        </div>
                        {results.map(r => {
                            const proc = PROCESS_META[r.process_type] || { color: '#888', icon: '🎵', label: r.process_type };
                            const isSelected = selectedIds.has(r.id);
                            return (
                                <div key={r.id} style={{
                                    display: 'flex', alignItems: 'center', gap: '0.75rem',
                                    background: isSelected ? `${proc.color}18` : 'var(--bg-surface)',
                                    border: `1px solid ${isSelected ? proc.color + '66' : proc.color + '33'}`,
                                    borderLeft: `3px solid ${proc.color}`,
                                    borderRadius: 'var(--radius-sm)',
                                    padding: '0.65rem 0.875rem',
                                    marginBottom: '0.5rem',
                                    transition: 'all 0.15s',
                                }}>
                                    <input
                                        type="checkbox"
                                        checked={isSelected}
                                        onChange={() => toggleSelect(r.id)}
                                        style={{ width: 15, height: 15, minWidth: 15, cursor: 'pointer', flexShrink: 0 }}
                                    />
                                    <span style={{ fontSize: '1rem', flexShrink: 0 }}>{proc.icon}</span>
                                    <span style={{ flex: 1, fontWeight: 500, fontSize: '0.875rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
                                        {r.filename}
                                    </span>
                                    <div style={{ display: 'flex', gap: '0.4rem', flexShrink: 0, alignItems: 'center' }}>
                                        {r.duration > 0 && (
                                            <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                                                {fmtDur(r.duration)}
                                            </span>
                                        )}
                                        <span style={{ fontSize: '0.78rem', fontWeight: 700, color: proc.color, whiteSpace: 'nowrap' }}>
                                            {(r.score * 100).toFixed(0)}%
                                        </span>
                                    </div>
                                </div>
                            );
                        })}
                        {hasMore && (
                            <button
                                className="btn btn-secondary"
                                style={{ width: '100%', marginTop: '0.25rem' }}
                                disabled={searching}
                                onClick={() => search(results.length)}
                            >
                                {searching ? '⏳ Loading…' : '+ 10 More'}
                            </button>
                        )}
                    </div>
                )}
            </div>

            {showDeleteConfirm && (
                <DeleteConfirmOverlay
                    count={selectedIds.size}
                    onConfirm={handleConfirmDelete}
                    onCancel={() => setShowDeleteConfirm(false)}
                    deleting={deletingBulk}
                />
            )}
        </div>
    );
}

/* ─── Upgrade wall (signed-in but no subscription) ──────────────────────── */

const PLANS = [
    {
        id: 'standard',
        name: 'Standard',
        price: '$9.99 / mo',
        desc: 'Unlimited use of all processing tools — no daily limits. Save up to 50 files.',
        color: '#7c3aed',
    },
    {
        id: 'premium',
        name: 'Premium',
        price: '$14.99 / mo',
        desc: 'Everything in Standard + unlimited file storage, AI Smart Search, and bulk upload.',
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
                    Every file you process is automatically saved to your personal library.
                    Upgrade to unlock access to your library and AI-powered similarity search.
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

/* ─── Premium feature overlay ───────────────────────────────────────────── */

function PremiumFeatureOverlay({ onClose, onUpgrade, featureName }) {
    return createPortal(
        <div className="modal-overlay" onClick={onClose} style={{ zIndex: 10001 }}>
            <div
                className="modal-content fade-in"
                style={{
                    maxWidth: '480px',
                    padding: '2.5rem 2rem',
                    textAlign: 'center',
                    background: 'linear-gradient(135deg, var(--bg-card) 0%, #fff 100%)',
                    borderRadius: 24,
                    boxShadow: '0 20px 50px rgba(0,0,0,0.15)',
                    position: 'relative'
                }}
                onClick={(e) => e.stopPropagation()}
            >
                <button
                    className="modal-close"
                    onClick={onClose}
                    style={{ top: '1rem', right: '1rem', background: 'var(--bg-surface)', borderRadius: '50%', width: 32, height: 32 }}
                >✕</button>

                <div style={{
                    fontSize: '4rem', marginBottom: '1.5rem',
                    filter: 'drop-shadow(0 10px 15px rgba(217, 119, 6, 0.3))'
                }}>💎</div>

                <h2 style={{ fontSize: '1.8rem', marginBottom: '1rem', background: 'linear-gradient(to right, #d97706, #f59e0b)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                    Premium Feature
                </h2>

                <p style={{ color: 'var(--text-secondary)', fontSize: '1.05rem', lineHeight: 1.6, marginBottom: '2rem' }}>
                    {featureName} is reserved for <strong>Premium</strong> tier members.
                    Unlock advanced AI capabilities and unlimited storage today.
                </p>

                <div style={{
                    background: 'var(--bg-surface)',
                    borderRadius: 16,
                    padding: '1.5rem',
                    textAlign: 'left',
                    marginBottom: '2rem',
                    border: '1px solid var(--border-bright)'
                }}>
                    <h4 style={{ margin: '0 0 1rem', fontSize: '0.9rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                        Premium Benefits:
                    </h4>
                    <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                        {[
                            'Unlimited Smart Database Storage',
                            'AI Smart Search & Retrieval',
                            'Bulk File Uploads',
                            'High-Priority Processing'
                        ].map(benefit => (
                            <li key={benefit} style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '0.95rem' }}>
                                <span style={{ color: '#059669', fontWeight: 'bold' }}>✓</span>
                                {benefit}
                            </li>
                        ))}
                    </ul>
                </div>

                <div style={{ display: 'flex', gap: '1rem', flexDirection: 'column' }}>
                    <button
                        className="btn btn-primary"
                        style={{
                            width: '100%',
                            padding: '1rem',
                            fontSize: '1.1rem',
                            fontWeight: 600,
                            borderRadius: 12,
                            background: '#d97706',
                            borderColor: '#d97706',
                            boxShadow: '0 4px 12px rgba(217, 119, 6, 0.4)'
                        }}
                        onClick={onUpgrade}
                    >
                        Join Premium — $14.99 / mo
                    </button>
                    <button
                        className="btn btn-secondary"
                        style={{ width: '100%', background: 'none', border: 'none', color: 'var(--text-muted)' }}
                        onClick={onClose}
                    >
                        Maybe later
                    </button>
                </div>
            </div>
        </div>,
        document.body
    );
}

/* ─── Main component ────────────────────────────────────────────────────── */

export default function MyFilesTab() {
    const { getToken, isSignedIn } = useAuth();
    const { profile, openSubModal, setFileCount, setIsProcessing } = useAuthContext();

    // isPremiumUser: must have both an active subscription AND premium tier
    const isPremiumUser = !!(profile?.subscription_active && profile?.subscription_type === 'premium');
    // canUpload: strictly premium only as per latest requirement
    const canUpload = isPremiumUser;

    const [premiumOverlayOpen, setPremiumOverlayOpen] = useState(false);
    const [premiumFeature, setPremiumFeature] = useState('');

    const [view, setView] = useState('files'); // 'files' | 'search'
    const [files, setFiles] = useState([]);
    const [filesLoading, setFilesLoading] = useState(false);
    const [filesError, setFilesError] = useState('');

    // Bulk upload state
    const [bulkUploading, setBulkUploading] = useState(false);
    const [bulkStatus, setBulkStatus] = useState('');
    const bulkInputRef = useRef(null);

    useEffect(() => {
        setIsProcessing(bulkUploading);
        return () => setIsProcessing(false);
    }, [bulkUploading, setIsProcessing]);

    // NowPlaying state
    const [nowPlaying, setNowPlaying] = useState(null); // {id, filename, process_type, subgroup, duration, blob, loading}
    const [loadingId, setLoadingId] = useState(null);

    // ── Fetch files ──────────────────────────────────────────────────────────
    const fetchFiles = useCallback(async () => {
        if (!isSignedIn) return;
        setFilesLoading(true);
        setFilesError('');
        try {
            const t = await getToken();
            const resp = await fetch(`${API_BASE}/files`, {
                headers: { Authorization: `Bearer ${t}` },
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            const fetched = data.files || [];

            setFiles(prev => {
                const isSame = fetched.length === prev.length && fetched.every((f, i) => f.id === prev[i]?.id);
                return isSame ? prev : fetched;
            });

            setFileCount(fetched.length);
            return fetched.length;
        } catch (e) {
            setFilesError(e.message);
            return undefined;
        } finally {
            setFilesLoading(false);
        }
    }, [isSignedIn, getToken, setFileCount]);

    useEffect(() => { fetchFiles(); }, [fetchFiles]);

    // ── Background-embedding poller ─────────────────────────────────────────
    // Processing tabs fire window.dispatchEvent(new CustomEvent('audioProcessed'))
    // and write localStorage.lastProcessedAt so this tab knows to start polling
    // until all background embeddings are committed to Qdrant.

    const filesLengthRef = useRef(0);
    const pollingTimerRef = useRef(null);
    const stableCountRef = useRef(0);
    const [isPolling, setIsPolling] = useState(false);

    const startPolling = useCallback(() => {
        if (pollingTimerRef.current) return; // already running
        stableCountRef.current = 0;
        setIsPolling(true);

        pollingTimerRef.current = setInterval(async () => {
            const nextLength = await fetchFiles();
            if (nextLength !== undefined) {
                const prev = filesLengthRef.current;
                if (nextLength > prev) {
                    stableCountRef.current = 0;
                } else {
                    stableCountRef.current += 1;
                    if (stableCountRef.current >= 3) {
                        clearInterval(pollingTimerRef.current);
                        pollingTimerRef.current = null;
                        setIsPolling(false);
                    }
                }
                filesLengthRef.current = nextLength;
            }
        }, 4000);

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

    // Cleanup timer on unmount
    useEffect(() => () => {
        if (pollingTimerRef.current) clearInterval(pollingTimerRef.current);
    }, []);

    // ── Load audio for playback ─────────────────────────────────────────────
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
            const t = await getToken();
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
    }, [nowPlaying, getToken]);

    // ── Flat ordered file list (matches FilesView render order) ─────────────
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

    // ── Navigate prev/next in the file list ─────────────────────────────────
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

    // ── Arrow-key navigation (only when Saved Files sub-tab is visible) ──────
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

    // ── Download a single file via authenticated fetch ───────────────────────
    const downloadFile = useCallback(async (file) => {
        try {
            const t = await getToken();
            const resp = await fetch(`${API_BASE}/files/${file.id}/audio`, {
                headers: { Authorization: `Bearer ${t}` },
            });
            if (!resp.ok) throw new Error(`API ${resp.status}`);
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = file.filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } catch (err) {
            alert(`Failed to download ${file.filename}: ${err.message}`);
        }
    }, [getToken]);

    // ── Delete a file (Qdrant + R2) ─────────────────────────────────────────
    const deleteFile = useCallback(async (fileId) => {
        try {
            const t = await getToken();
            const resp = await fetch(`${API_BASE}/files/${fileId}`, {
                method: 'DELETE',
                headers: { Authorization: `Bearer ${t}` },
            });
            if (!resp.ok) {
                const text = await resp.text();
                throw new Error(`API ${resp.status}: ${text}`);
            }
            // Optimistically remove from local state
            setFiles(prev => prev.filter(f => f.id !== fileId));
            // Close the player if this file was playing
            if (nowPlaying?.id === fileId) setNowPlaying(null);
        } catch (err) {
            alert(`Failed to remove file: ${err.message}`);
        }
    }, [getToken, nowPlaying]);

    // ── Bulk upload (standard+ — enforced by backend quota) ──────────────────
    const handleUploadClick = useCallback(() => {
        if (!isPremiumUser) {
            setPremiumFeature('Bulk File Upload');
            setPremiumOverlayOpen(true);
            return;
        }
        bulkInputRef.current?.click();
    }, [isPremiumUser]);

    const handleBulkUpload = useCallback(async (e) => {
        const selectedFiles = Array.from(e.target.files || []);
        if (selectedFiles.length === 0) return;
        // Reset the input so the same selection can be re-triggered
        e.target.value = '';

        setBulkUploading(true);
        setBulkStatus(`Uploading ${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''}…`);
        try {
            const t = await getToken();
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
    }, [getToken, fetchFiles, startPolling]);

    // ── Auth gates ───────────────────────────────────────────────────────────

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
                                : isPremiumUser
                                    ? `${files.length} file${files.length !== 1 ? 's' : ''} stored · unlimited`
                                    : `${files.length} / 50 file${files.length !== 1 ? 's' : ''} stored`}
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

                        {/* Smart Search sub-tab (premium only) */}
                        <button
                            className={`btn ${view === 'search' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ padding: '0.4rem 1rem', fontSize: '0.82rem', position: 'relative' }}
                            onClick={() => {
                                if (!isPremiumUser) {
                                    setPremiumFeature('AI Smart Search');
                                    setPremiumOverlayOpen(true);
                                    return;
                                }
                                setView('search');
                            }}
                        >
                            🔍 Smart Search
                            {!isPremiumUser && (
                                <span style={{
                                    marginLeft: '0.35rem', fontSize: '0.62rem',
                                    background: '#f59e0b', color: '#fff',
                                    borderRadius: 'var(--radius-full)', padding: '0.05rem 0.35rem', fontWeight: 700,
                                }}>PREMIUM</span>
                            )}
                        </button>

                        {/* Refresh (only for Saved Files) */}
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

            {/* ── Error / status banners ──────────────────────────── */}
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

            {/* ── Saved Files view ──────────────────────────────── */}
            {view === 'files' && (
                <FilesView
                    files={files}
                    loading={filesLoading}
                    error={filesError}
                    nowPlaying={nowPlaying}
                    loadingId={loadingId}
                    onPlay={handlePlay}
                    onDelete={deleteFile}
                    onDownloadFile={downloadFile}
                    onUploadClick={handleUploadClick}
                    bulkUploading={bulkUploading}
                    canUpload={canUpload}
                />
            )}

            {/* ── Smart Search view (premium only) ─────────────── */}
            {view === 'search' && isPremiumUser && (
                <SmartSearch
                    getToken={getToken}
                    onDelete={deleteFile}
                    onDownloadFile={downloadFile}
                    isPremiumUser={isPremiumUser}
                    onPremiumAction={(name) => {
                        setPremiumFeature(name);
                        setPremiumOverlayOpen(true);
                    }}
                />
            )}

            {premiumOverlayOpen && (
                <PremiumFeatureOverlay
                    featureName={premiumFeature}
                    onClose={() => setPremiumOverlayOpen(false)}
                    onUpgrade={() => {
                        setPremiumOverlayOpen(false);
                        openSubModal('premium');
                    }}
                />
            )}

            {/* Hidden file input for bulk upload */}
            <input
                ref={bulkInputRef}
                type="file"
                multiple
                hidden
                accept=".wav,.flac,.mp3,.aac,.ogg"
                onChange={handleBulkUpload}
            />

            {/* ── NowPlaying bar (portalled to document.body) ─── */}
            <NowPlayingBar
                nowPlaying={nowPlaying}
                onClose={() => setNowPlaying(null)}
                onPrev={() => navigateFiles(-1)}
                onNext={() => navigateFiles(1)}
            />
        </div>
    );
}
