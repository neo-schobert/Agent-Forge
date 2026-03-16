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

const STATUS_CONFIG = {
  pending:  { label: 'Pending',  color: COLORS.textSecondary, bg: `${COLORS.textSecondary}22`, pulse: false },
  running:  { label: 'Running',  color: COLORS.primary,       bg: `${COLORS.primary}22`,       pulse: true  },
  done:     { label: 'Done',     color: COLORS.success,       bg: `${COLORS.success}22`,       pulse: false },
  completed:{ label: 'Done',     color: COLORS.success,       bg: `${COLORS.success}22`,       pulse: false },
  failed:   { label: 'Failed',   color: COLORS.error,         bg: `${COLORS.error}22`,         pulse: false },
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

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '5px',
      padding: '3px 10px',
      borderRadius: '20px',
      fontSize: '12px',
      fontWeight: 600,
      background: cfg.bg,
      color: cfg.color,
      flexShrink: 0,
    }}>
      {cfg.pulse && (
        <span style={{
          width: '6px',
          height: '6px',
          borderRadius: '50%',
          background: cfg.color,
          display: 'inline-block',
          animation: 'pulse 1.5s infinite',
        }} />
      )}
      {cfg.label}
    </span>
  );
}

const TABS = [
  { key: 'all',     label: 'All' },
  { key: 'running', label: 'Running' },
  { key: 'done',    label: 'Done' },
  { key: 'failed',  label: 'Failed' },
];

export default function TaskList({ onSelectTask }) {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('all');

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch('/api/tasks');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTasks(Array.isArray(data) ? data : (data.tasks || []));
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTasks();
    const interval = setInterval(fetchTasks, 10000);
    return () => clearInterval(interval);
  }, [fetchTasks]);

  const filtered = tasks.filter(t => {
    if (activeTab === 'all') return true;
    if (activeTab === 'done') return t.status === 'done' || t.status === 'completed';
    return t.status === activeTab;
  });

  const tabCount = (key) => {
    if (key === 'all') return tasks.length;
    if (key === 'done') return tasks.filter(t => t.status === 'done' || t.status === 'completed').length;
    return tasks.filter(t => t.status === key).length;
  };

  return (
    <div style={{ fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(1.3); }
        }
        .task-row:hover { background: ${COLORS.surface2} !important; }
      `}</style>

      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ color: COLORS.text, fontSize: '22px', fontWeight: 700, margin: 0 }}>Tasks</h1>
        <p style={{ color: COLORS.textSecondary, fontSize: '14px', marginTop: '4px', marginBottom: 0 }}>All agent tasks and their status</p>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '4px', marginBottom: '16px', background: COLORS.surface, borderRadius: '8px', padding: '4px', width: 'fit-content' }}>
        {TABS.map(tab => {
          const count = tabCount(tab.key);
          const isActive = activeTab === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              style={{
                padding: '6px 14px',
                background: isActive ? COLORS.primary : 'transparent',
                border: 'none',
                borderRadius: '6px',
                color: isActive ? '#fff' : COLORS.textSecondary,
                cursor: 'pointer',
                fontSize: '13px',
                fontWeight: isActive ? 600 : 400,
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                transition: 'all 0.15s',
              }}
            >
              {tab.label}
              <span style={{
                padding: '1px 6px',
                borderRadius: '10px',
                fontSize: '11px',
                background: isActive ? 'rgba(255,255,255,0.25)' : COLORS.surface2,
                color: isActive ? '#fff' : COLORS.textSecondary,
              }}>
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Table */}
      <div style={{ background: COLORS.surface, borderRadius: '10px', border: `1px solid ${COLORS.border}`, overflow: 'hidden' }}>
        {loading && (
          <div style={{ padding: '40px', textAlign: 'center', color: COLORS.textSecondary }}>Loading tasks...</div>
        )}
        {error && (
          <div style={{ padding: '20px', color: COLORS.error, fontSize: '14px' }}>Error: {error}</div>
        )}
        {!loading && filtered.length === 0 && (
          <div style={{ padding: '40px', textAlign: 'center', color: COLORS.textSecondary, fontSize: '14px' }}>
            No {activeTab !== 'all' ? activeTab : ''} tasks found.
          </div>
        )}
        {!loading && filtered.length > 0 && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${COLORS.border}` }}>
                {['Status', 'Issue', 'Title', 'Branch', 'Created', 'PR'].map(h => (
                  <th key={h} style={{
                    padding: '10px 16px',
                    textAlign: 'left',
                    color: COLORS.textSecondary,
                    fontSize: '12px',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    whiteSpace: 'nowrap',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((task, idx) => (
                <tr
                  key={task.id || task.task_id || idx}
                  className="task-row"
                  onClick={() => onSelectTask && onSelectTask(task)}
                  style={{
                    borderBottom: idx < filtered.length - 1 ? `1px solid ${COLORS.border}22` : 'none',
                    cursor: 'pointer',
                    background: 'transparent',
                    transition: 'background 0.1s',
                  }}
                >
                  <td style={{ padding: '12px 16px' }}>
                    <StatusBadge status={task.status} />
                  </td>
                  <td style={{ padding: '12px 16px', color: COLORS.textSecondary, fontSize: '13px', whiteSpace: 'nowrap' }}>
                    {task.issue_number != null ? `#${task.issue_number}` : (task.issue ? `#${task.issue}` : '—')}
                  </td>
                  <td style={{ padding: '12px 16px' }}>
                    <div style={{ color: COLORS.text, fontSize: '14px', maxWidth: '280px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {task.title || task.name || task.description || `Task ${task.id || task.task_id}`}
                    </div>
                  </td>
                  <td style={{ padding: '12px 16px' }}>
                    {task.branch ? (
                      <code style={{ color: COLORS.primary, fontSize: '12px', background: `${COLORS.primary}18`, padding: '2px 6px', borderRadius: '4px' }}>
                        {task.branch}
                      </code>
                    ) : (
                      <span style={{ color: COLORS.textSecondary, fontSize: '13px' }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: '12px 16px', color: COLORS.textSecondary, fontSize: '13px', whiteSpace: 'nowrap' }}>
                    {timeAgo(task.created_at || task.createdAt)}
                  </td>
                  <td style={{ padding: '12px 16px' }}>
                    {(task.pr_url || task.pr_number) ? (
                      <a
                        href={task.pr_url || '#'}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={e => e.stopPropagation()}
                        style={{ color: COLORS.primary, fontSize: '13px', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '4px' }}
                      >
                        #{task.pr_number || '—'} ↗
                      </a>
                    ) : (
                      <span style={{ color: COLORS.textSecondary, fontSize: '13px' }}>—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
