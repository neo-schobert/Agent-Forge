import React, { useState, useEffect, useCallback } from 'react';

const COLORS = {
  bg: '#0f172a',
  surface: '#1e293b',
  surface2: '#334155',
  primary: '#3b82f6',
  success: '#22c55e',
  warning: '#f59e0b',
  error: '#ef4444',
  text: '#f1f5f9',
  textSecondary: '#94a3b8',
  border: '#334155',
};

function timeAgo(timestamp) {
  if (!timestamp) return '';
  const now = Date.now();
  const ts = typeof timestamp === 'number' ? timestamp : new Date(timestamp).getTime();
  const diff = Math.floor((now - ts) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const STATUS_CONFIG = {
  open:   { label: 'Open',   color: COLORS.success, bg: `${COLORS.success}22` },
  merged: { label: 'Merged', color: '#a855f7',       bg: '#a855f722'           },
  closed: { label: 'Closed', color: COLORS.error,   bg: `${COLORS.error}22`   },
};

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.open;
  return (
    <span style={{ padding: '3px 10px', borderRadius: '20px', fontSize: '12px', fontWeight: 600, background: cfg.bg, color: cfg.color }}>
      {cfg.label}
    </span>
  );
}

function DiffViewer({ diff }) {
  if (!diff) return <div style={{ color: COLORS.textSecondary, fontSize: '13px', padding: '20px', fontFamily: 'monospace' }}>No diff available.</div>;

  const lines = diff.split('\n');

  return (
    <div style={{ fontFamily: 'monospace', fontSize: '12px', lineHeight: '1.6', overflow: 'auto' }}>
      {lines.map((line, i) => {
        let bg = 'transparent';
        let color = COLORS.text;
        if (line.startsWith('+') && !line.startsWith('+++')) {
          bg = '#22c55e18';
          color = '#86efac';
        } else if (line.startsWith('-') && !line.startsWith('---')) {
          bg = '#ef444418';
          color = '#fca5a5';
        } else if (line.startsWith('@@')) {
          bg = `${COLORS.primary}18`;
          color = '#93c5fd';
        } else if (line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('--- ') || line.startsWith('+++ ')) {
          color = COLORS.textSecondary;
        }
        return (
          <div key={i} style={{ background: bg, padding: '0 12px', whiteSpace: 'pre', color, minWidth: 'max-content' }}>
            {line || ' '}
          </div>
        );
      })}
    </div>
  );
}

function PRExpandedView({ pr, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [merging, setMerging] = useState(false);
  const [mergeResult, setMergeResult] = useState(null);
  const [showReject, setShowReject] = useState(false);
  const [rejectComment, setRejectComment] = useState('');
  const [rejecting, setRejecting] = useState(false);
  const [rejectResult, setRejectResult] = useState(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`/api/prs/${pr.number}`);
        if (res.ok) setDetail(await res.json());
      } catch { /* ignore */ }
      setLoading(false);
    };
    load();
  }, [pr.number]);

  const handleMerge = async () => {
    setMerging(true);
    setMergeResult(null);
    try {
      const res = await fetch(`/api/prs/${pr.number}/merge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ merge_style: 'merge' }),
      });
      const json = await res.json().catch(() => ({}));
      if (res.ok) {
        setMergeResult({ success: true, message: json.message || 'PR merged successfully.' });
      } else {
        setMergeResult({ success: false, message: json.error || json.message || `HTTP ${res.status}` });
      }
    } catch (err) {
      setMergeResult({ success: false, message: err.message });
    } finally {
      setMerging(false);
    }
  };

  const handleReject = async () => {
    if (!rejectComment.trim()) return;
    setRejecting(true);
    setRejectResult(null);
    try {
      const res = await fetch(`/api/prs/${pr.number}/comment`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body: rejectComment }),
      });
      const json = await res.json().catch(() => ({}));
      if (res.ok) {
        setRejectResult({ success: true, message: 'Comment posted.' });
        setShowReject(false);
        setRejectComment('');
      } else {
        setRejectResult({ success: false, message: json.error || `HTTP ${res.status}` });
      }
    } catch (err) {
      setRejectResult({ success: false, message: err.message });
    } finally {
      setRejecting(false);
    }
  };

  const data = detail || pr;

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.7)',
      display: 'flex',
      alignItems: 'flex-start',
      justifyContent: 'center',
      zIndex: 200,
      padding: '40px 20px',
      overflowY: 'auto',
    }}>
      <div style={{
        background: COLORS.surface,
        borderRadius: '12px',
        border: `1px solid ${COLORS.border}`,
        width: '100%',
        maxWidth: '860px',
        boxShadow: '0 24px 64px rgba(0,0,0,0.6)',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{ padding: '20px 24px', borderBottom: `1px solid ${COLORS.border}`, display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
              <span style={{ color: COLORS.textSecondary, fontSize: '14px' }}>#{pr.number}</span>
              <StatusBadge status={data.state || data.status || 'open'} />
            </div>
            <h2 style={{ color: COLORS.text, fontSize: '18px', fontWeight: 700, margin: 0 }}>{data.title}</h2>
            {(data.head?.label || data.head_branch) && (
              <div style={{ color: COLORS.textSecondary, fontSize: '13px', marginTop: '6px' }}>
                <code style={{ color: COLORS.primary, background: `${COLORS.primary}18`, padding: '2px 6px', borderRadius: '4px' }}>
                  {data.head?.label || data.head_branch}
                </code>
                {' → '}
                <code style={{ color: COLORS.textSecondary, background: COLORS.surface2, padding: '2px 6px', borderRadius: '4px' }}>
                  {data.base?.label || data.base_branch || 'main'}
                </code>
              </div>
            )}
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: COLORS.textSecondary, cursor: 'pointer', fontSize: '20px', padding: '4px', flexShrink: 0 }}>✕</button>
        </div>

        {/* Description */}
        {data.body && (
          <div style={{ padding: '16px 24px', borderBottom: `1px solid ${COLORS.border}` }}>
            <div style={{ color: COLORS.textSecondary, fontSize: '12px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '8px' }}>Description</div>
            <div style={{ color: COLORS.text, fontSize: '14px', lineHeight: '1.6', whiteSpace: 'pre-wrap' }}>{data.body}</div>
          </div>
        )}

        {/* Diff */}
        <div style={{ borderBottom: `1px solid ${COLORS.border}` }}>
          <div style={{ padding: '12px 24px', borderBottom: `1px solid ${COLORS.border}`, background: COLORS.surface2 }}>
            <span style={{ color: COLORS.textSecondary, fontSize: '12px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Diff</span>
          </div>
          <div style={{ maxHeight: '400px', overflowY: 'auto', background: '#0a0f1e' }}>
            {loading ? (
              <div style={{ padding: '20px', color: COLORS.textSecondary, fontSize: '13px' }}>Loading diff...</div>
            ) : (
              <DiffViewer diff={data.diff || data.patch} />
            )}
          </div>
        </div>

        {/* Actions */}
        <div style={{ padding: '20px 24px' }}>
          {mergeResult && (
            <div style={{
              padding: '10px 14px',
              borderRadius: '6px',
              fontSize: '13px',
              background: mergeResult.success ? `${COLORS.success}18` : `${COLORS.error}18`,
              color: mergeResult.success ? COLORS.success : COLORS.error,
              border: `1px solid ${mergeResult.success ? COLORS.success : COLORS.error}44`,
              marginBottom: '14px',
            }}>
              {mergeResult.message}
            </div>
          )}
          {rejectResult && (
            <div style={{
              padding: '10px 14px',
              borderRadius: '6px',
              fontSize: '13px',
              background: rejectResult.success ? `${COLORS.success}18` : `${COLORS.error}18`,
              color: rejectResult.success ? COLORS.success : COLORS.error,
              border: `1px solid ${rejectResult.success ? COLORS.success : COLORS.error}44`,
              marginBottom: '14px',
            }}>
              {rejectResult.message}
            </div>
          )}

          {showReject ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <textarea
                value={rejectComment}
                onChange={e => setRejectComment(e.target.value)}
                placeholder="Write your rejection reason or requested changes..."
                rows={4}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  background: COLORS.surface2,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: '6px',
                  color: COLORS.text,
                  fontSize: '14px',
                  resize: 'vertical',
                  outline: 'none',
                  fontFamily: 'system-ui, -apple-system, sans-serif',
                  boxSizing: 'border-box',
                }}
              />
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  onClick={handleReject}
                  disabled={!rejectComment.trim() || rejecting}
                  style={{
                    padding: '8px 20px',
                    background: COLORS.error,
                    border: 'none',
                    borderRadius: '6px',
                    color: '#fff',
                    fontWeight: 600,
                    fontSize: '14px',
                    cursor: !rejectComment.trim() || rejecting ? 'not-allowed' : 'pointer',
                    opacity: !rejectComment.trim() || rejecting ? 0.5 : 1,
                  }}
                >
                  {rejecting ? 'Posting...' : 'Post Comment'}
                </button>
                <button
                  onClick={() => setShowReject(false)}
                  style={{ padding: '8px 20px', background: 'transparent', border: `1px solid ${COLORS.border}`, borderRadius: '6px', color: COLORS.textSecondary, fontSize: '14px', cursor: 'pointer' }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
              <button
                onClick={handleMerge}
                disabled={merging || (data.state === 'merged')}
                style={{
                  padding: '10px 22px',
                  background: COLORS.success,
                  border: 'none',
                  borderRadius: '6px',
                  color: '#fff',
                  fontWeight: 600,
                  fontSize: '14px',
                  cursor: merging || data.state === 'merged' ? 'not-allowed' : 'pointer',
                  opacity: merging || data.state === 'merged' ? 0.5 : 1,
                }}
              >
                {merging ? 'Merging...' : data.state === 'merged' ? 'Already Merged' : 'Merge PR'}
              </button>
              <button
                onClick={() => setShowReject(true)}
                style={{
                  padding: '10px 22px',
                  background: 'transparent',
                  border: `1px solid ${COLORS.error}`,
                  borderRadius: '6px',
                  color: COLORS.error,
                  fontWeight: 600,
                  fontSize: '14px',
                  cursor: 'pointer',
                }}
              >
                Reject with Comment
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PRViewer() {
  const [prs, setPRs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedPR, setSelectedPR] = useState(null);

  const fetchPRs = useCallback(async () => {
    try {
      const res = await fetch('/api/prs');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setPRs(Array.isArray(data) ? data : (data.prs || data.pull_requests || []));
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPRs();
    const iv = setInterval(fetchPRs, 30000);
    return () => clearInterval(iv);
  }, [fetchPRs]);

  return (
    <div style={{ fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      {selectedPR && (
        <PRExpandedView pr={selectedPR} onClose={() => setSelectedPR(null)} />
      )}

      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ color: COLORS.text, fontSize: '22px', fontWeight: 700, margin: 0 }}>Pull Requests</h1>
        <p style={{ color: COLORS.textSecondary, fontSize: '14px', marginTop: '4px', marginBottom: 0 }}>Review and manage agent-created pull requests</p>
      </div>

      <div style={{ background: COLORS.surface, borderRadius: '10px', border: `1px solid ${COLORS.border}`, overflow: 'hidden' }}>
        {loading && (
          <div style={{ padding: '40px', textAlign: 'center', color: COLORS.textSecondary }}>Loading pull requests...</div>
        )}
        {error && (
          <div style={{ padding: '20px', color: COLORS.error, fontSize: '14px' }}>Error: {error}</div>
        )}
        {!loading && prs.length === 0 && (
          <div style={{ padding: '40px', textAlign: 'center', color: COLORS.textSecondary, fontSize: '14px' }}>No pull requests found.</div>
        )}
        {!loading && prs.length > 0 && (
          <div>
            {prs.map((pr, idx) => (
              <div
                key={pr.number || pr.id || idx}
                onClick={() => setSelectedPR(pr)}
                style={{
                  padding: '16px 20px',
                  borderBottom: idx < prs.length - 1 ? `1px solid ${COLORS.border}` : 'none',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '16px',
                  transition: 'background 0.1s',
                }}
                onMouseEnter={e => e.currentTarget.style.background = COLORS.surface2}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
              >
                <span style={{ color: COLORS.textSecondary, fontSize: '14px', minWidth: '40px', flexShrink: 0 }}>#{pr.number}</span>

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: COLORS.text, fontSize: '14px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {pr.title}
                  </div>
                  {(pr.head?.label || pr.head_branch) && (
                    <div style={{ color: COLORS.textSecondary, fontSize: '12px', marginTop: '3px' }}>
                      <code style={{ color: COLORS.primary }}>{pr.head?.label || pr.head_branch}</code>
                      {' → '}
                      <code>{pr.base?.label || pr.base_branch || 'main'}</code>
                    </div>
                  )}
                </div>

                <StatusBadge status={pr.state || pr.status || 'open'} />

                <span style={{ color: COLORS.textSecondary, fontSize: '12px', flexShrink: 0 }}>
                  {timeAgo(pr.created_at || pr.createdAt)}
                </span>

                <span style={{ color: COLORS.primary, fontSize: '13px', flexShrink: 0 }}>→</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
