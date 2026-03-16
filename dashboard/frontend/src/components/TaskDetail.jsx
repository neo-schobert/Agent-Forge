import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useWebSocket } from '../hooks/useWebSocket.js';

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

const AGENT_STEPS = [
  { key: 'supervisor', label: 'Supervisor' },
  { key: 'architect',  label: 'Architect'  },
  { key: 'coder',      label: 'Coder'      },
  { key: 'tester',     label: 'Tester'     },
  { key: 'reviewer',   label: 'Reviewer'   },
];

const STATUS_COLORS = {
  waiting: COLORS.textSecondary,
  active:  COLORS.primary,
  done:    COLORS.success,
  error:   COLORS.error,
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

function formatDuration(ms) {
  if (!ms) return '';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  return `${m}m ${rs}s`;
}

function formatElapsed(startedAt) {
  if (!startedAt) return '';
  const elapsed = Date.now() - new Date(startedAt).getTime();
  return formatDuration(elapsed);
}

function LogLine({ line }) {
  const level = line.level || detectLevel(line.text || line.message || '');
  const text = line.text || line.message || line;
  const color = level === 'error' ? COLORS.error
    : level === 'warning' || level === 'warn' ? COLORS.warning
    : level === 'debug' ? '#475569'
    : COLORS.text;

  return (
    <div style={{
      display: 'flex',
      gap: '10px',
      padding: '2px 0',
      fontFamily: 'monospace',
      fontSize: '12px',
      lineHeight: '1.6',
    }}>
      {line.timestamp && (
        <span style={{ color: COLORS.textSecondary, flexShrink: 0, userSelect: 'none' }}>
          {new Date(line.timestamp).toLocaleTimeString()}
        </span>
      )}
      {level && level !== 'info' && (
        <span style={{ color, flexShrink: 0, minWidth: '50px', textTransform: 'uppercase', fontWeight: 600, fontSize: '10px', paddingTop: '1px' }}>
          [{level}]
        </span>
      )}
      <span style={{ color, wordBreak: 'break-all', flex: 1 }}>{String(text)}</span>
    </div>
  );
}

function detectLevel(text) {
  const t = text.toLowerCase();
  if (t.includes('[error]') || t.startsWith('error') || t.includes('exception')) return 'error';
  if (t.includes('[warn') || t.startsWith('warn')) return 'warning';
  if (t.includes('[debug]') || t.startsWith('debug')) return 'debug';
  return 'info';
}

function StatusBadge({ status }) {
  const map = {
    pending:   { label: 'Pending',  bg: `${COLORS.textSecondary}22`, color: COLORS.textSecondary },
    running:   { label: 'Running',  bg: `${COLORS.primary}22`,       color: COLORS.primary       },
    done:      { label: 'Done',     bg: `${COLORS.success}22`,       color: COLORS.success       },
    completed: { label: 'Done',     bg: `${COLORS.success}22`,       color: COLORS.success       },
    failed:    { label: 'Failed',   bg: `${COLORS.error}22`,         color: COLORS.error         },
  };
  const cfg = map[status] || map.pending;
  return (
    <span style={{ padding: '4px 12px', borderRadius: '20px', fontSize: '13px', fontWeight: 600, background: cfg.bg, color: cfg.color }}>
      {cfg.label}
    </span>
  );
}

export default function TaskDetail({ taskId, onBack }) {
  const [task, setTask] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [logs, setLogs] = useState([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [elapsed, setElapsed] = useState('');
  const logEndRef = useRef(null);
  const logContainerRef = useRef(null);

  const { lastMessage } = useWebSocket(taskId ? `/ws/tasks/${taskId}/logs` : null);

  useEffect(() => {
    if (!lastMessage) return;
    const msg = lastMessage.data;
    if (typeof msg === 'string') {
      setLogs(prev => [...prev, { text: msg, timestamp: Date.now() }]);
    } else if (msg && typeof msg === 'object') {
      setLogs(prev => [...prev, { ...msg, timestamp: msg.timestamp || Date.now() }]);
    }
  }, [lastMessage]);

  useEffect(() => {
    if (autoScroll && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoScroll]);

  const handleLogScroll = () => {
    const el = logContainerRef.current;
    if (!el) return;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setAutoScroll(isAtBottom);
  };

  const fetchTask = useCallback(async () => {
    if (!taskId) return;
    try {
      const res = await fetch(`/api/tasks/${taskId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setTask(await res.json());
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    fetchTask();
    const interval = setInterval(fetchTask, 5000);
    return () => clearInterval(interval);
  }, [fetchTask]);

  useEffect(() => {
    if (!task?.started_at && !task?.created_at) return;
    if (task?.status === 'done' || task?.status === 'completed' || task?.status === 'failed') return;
    const iv = setInterval(() => {
      setElapsed(formatElapsed(task.started_at || task.created_at));
    }, 1000);
    setElapsed(formatElapsed(task.started_at || task.created_at));
    return () => clearInterval(iv);
  }, [task?.started_at, task?.created_at, task?.status]);

  if (loading) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: COLORS.textSecondary, fontFamily: 'system-ui, -apple-system, sans-serif' }}>
        Loading task...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: '20px', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
        <button onClick={onBack} style={backBtnStyle}>← Back</button>
        <div style={{ color: COLORS.error, fontSize: '14px', marginTop: '16px' }}>Error: {error}</div>
      </div>
    );
  }

  if (!task) return null;

  const agentSteps = task.agent_steps || task.steps || {};
  const isResumed = task.resumed === true || task.crash_recovered === true || task.checkpoint_restored === true;
  const costData = task.cost || task.usage || {};
  const prUrl = task.pr_url;
  const prNumber = task.pr_number;

  // Build step statuses
  const stepStatuses = AGENT_STEPS.map(step => {
    const stepData = agentSteps[step.key] || {};
    return {
      ...step,
      status: stepData.status || (task.current_agent === step.key ? 'active' : stepData.completed ? 'done' : stepData.error ? 'error' : 'waiting'),
      duration: stepData.duration,
      model: stepData.model || '',
      error: stepData.error,
    };
  });

  // Determine current active index
  const activeIdx = stepStatuses.findIndex(s => s.status === 'active');

  return (
    <div style={{ fontFamily: 'system-ui, -apple-system, sans-serif', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <style>{`
        @keyframes pulse-dot {
          0%, 100% { opacity: 1; box-shadow: 0 0 0 0 ${COLORS.primary}88; }
          50% { opacity: 0.8; box-shadow: 0 0 0 6px ${COLORS.primary}00; }
        }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>

      {/* Header */}
      <div>
        <button onClick={onBack} style={backBtnStyle}>← Back to Tasks</button>
        <div style={{ marginTop: '16px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', marginBottom: '6px' }}>
              {task.issue_number != null && (
                <span style={{ color: COLORS.textSecondary, fontSize: '14px' }}>#{task.issue_number}</span>
              )}
              <StatusBadge status={task.status} />
              {isResumed && (
                <span style={{
                  padding: '3px 10px',
                  borderRadius: '20px',
                  fontSize: '12px',
                  fontWeight: 600,
                  background: `${COLORS.warning}22`,
                  color: COLORS.warning,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}>
                  ⚡ Crash Recovery
                </span>
              )}
              {task.iteration_count != null && task.iteration_count > 1 && (
                <span style={{
                  padding: '3px 10px',
                  borderRadius: '20px',
                  fontSize: '12px',
                  fontWeight: 600,
                  background: `${COLORS.textSecondary}22`,
                  color: COLORS.textSecondary,
                }}>
                  Iteration #{task.iteration_count}
                </span>
              )}
            </div>
            <h1 style={{ color: COLORS.text, fontSize: '20px', fontWeight: 700, margin: 0 }}>
              {task.title || task.name || `Task ${taskId}`}
            </h1>
            {task.branch && (
              <code style={{ color: COLORS.primary, fontSize: '12px', background: `${COLORS.primary}18`, padding: '2px 8px', borderRadius: '4px', marginTop: '6px', display: 'inline-block' }}>
                {task.branch}
              </code>
            )}
          </div>
          <div style={{ textAlign: 'right', color: COLORS.textSecondary, fontSize: '13px', flexShrink: 0 }}>
            {elapsed && <div style={{ color: COLORS.text, fontWeight: 600, fontSize: '16px' }}>{elapsed}</div>}
            {(task.started_at || task.created_at) && (
              <div>{timeAgo(task.started_at || task.created_at)}</div>
            )}
          </div>
        </div>
      </div>

      {/* Agent Timeline */}
      <div style={{ background: COLORS.surface, borderRadius: '10px', padding: '20px', border: `1px solid ${COLORS.border}` }}>
        <div style={{ color: COLORS.textSecondary, fontSize: '12px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '16px' }}>
          Agent Timeline
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0', overflowX: 'auto', paddingBottom: '4px' }}>
          {stepStatuses.map((step, idx) => {
            const isActive = step.status === 'active';
            const isDone = step.status === 'done';
            const isError = step.status === 'error';
            const isWaiting = step.status === 'waiting';
            const dotColor = isActive ? COLORS.primary : isDone ? COLORS.success : isError ? COLORS.error : COLORS.surface2;
            const textColor = isActive ? COLORS.primary : isDone ? COLORS.success : isError ? COLORS.error : COLORS.textSecondary;

            return (
              <React.Fragment key={step.key}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: '100px', flex: 1 }}>
                  <div style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '50%',
                    background: dotColor,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '14px',
                    flexShrink: 0,
                    animation: isActive ? 'pulse-dot 1.8s infinite' : undefined,
                    border: isWaiting ? `2px solid ${COLORS.border}` : 'none',
                  }}>
                    {isDone && <span style={{ color: '#fff', fontSize: '14px' }}>✓</span>}
                    {isError && <span style={{ color: '#fff', fontSize: '14px' }}>✗</span>}
                    {isActive && <span style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#fff', display: 'block' }} />}
                  </div>
                  <div style={{ color: textColor, fontSize: '12px', fontWeight: isActive ? 700 : 500, marginTop: '8px', textAlign: 'center' }}>
                    {step.label}
                  </div>
                  {step.duration && (
                    <div style={{ color: COLORS.textSecondary, fontSize: '11px', marginTop: '2px' }}>
                      {formatDuration(step.duration)}
                    </div>
                  )}
                  {step.model && (
                    <div style={{ color: COLORS.textSecondary, fontSize: '10px', marginTop: '2px', maxWidth: '90px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'center' }}>
                      {step.model}
                    </div>
                  )}
                </div>
                {idx < stepStatuses.length - 1 && (
                  <div style={{
                    flex: 1,
                    height: '2px',
                    background: isDone ? COLORS.success : COLORS.surface2,
                    marginTop: '15px',
                    minWidth: '20px',
                    alignSelf: 'flex-start',
                  }} />
                )}
              </React.Fragment>
            );
          })}
        </div>
      </div>

      {/* Cost Panel */}
      {(costData.total_tokens || costData.cost || costData.prompt_tokens) && (
        <div style={{ background: COLORS.surface, borderRadius: '10px', padding: '20px', border: `1px solid ${COLORS.border}` }}>
          <div style={{ color: COLORS.textSecondary, fontSize: '12px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '14px' }}>
            Token Usage & Cost
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: '12px' }}>
            {[
              { label: 'Prompt Tokens', value: costData.prompt_tokens ?? costData.input_tokens },
              { label: 'Completion Tokens', value: costData.completion_tokens ?? costData.output_tokens },
              { label: 'Total Tokens', value: costData.total_tokens },
              { label: 'Estimated Cost', value: costData.cost ? `$${parseFloat(costData.cost).toFixed(6)}` : null },
            ].filter(i => i.value != null).map(item => (
              <div key={item.label} style={{ background: COLORS.surface2, borderRadius: '8px', padding: '12px' }}>
                <div style={{ color: COLORS.textSecondary, fontSize: '11px', marginBottom: '4px' }}>{item.label}</div>
                <div style={{ color: COLORS.text, fontSize: '18px', fontWeight: 700 }}>
                  {typeof item.value === 'number' ? item.value.toLocaleString() : item.value}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* PR Link */}
      {(prUrl || prNumber) && (
        <div style={{ background: COLORS.surface, borderRadius: '10px', padding: '16px 20px', border: `1px solid ${COLORS.border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ color: COLORS.text, fontWeight: 600, fontSize: '14px' }}>Pull Request Created</div>
            {prNumber && <div style={{ color: COLORS.textSecondary, fontSize: '13px' }}>#{prNumber}</div>}
          </div>
          <a
            href={prUrl || '#'}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              padding: '8px 18px',
              background: COLORS.primary,
              color: '#fff',
              borderRadius: '6px',
              textDecoration: 'none',
              fontSize: '13px',
              fontWeight: 600,
            }}
          >
            View PR in Forgejo ↗
          </a>
        </div>
      )}

      {/* Live Log Stream */}
      <div style={{ background: COLORS.surface, borderRadius: '10px', border: `1px solid ${COLORS.border}`, overflow: 'hidden' }}>
        <div style={{
          padding: '12px 20px',
          borderBottom: `1px solid ${COLORS.border}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <div style={{ color: COLORS.textSecondary, fontSize: '12px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Live Logs
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ color: COLORS.textSecondary, fontSize: '12px' }}>{logs.length} lines</span>
            <button
              onClick={() => setAutoScroll(v => !v)}
              style={{
                padding: '3px 10px',
                background: autoScroll ? `${COLORS.success}22` : COLORS.surface2,
                border: `1px solid ${autoScroll ? COLORS.success : COLORS.border}`,
                borderRadius: '4px',
                color: autoScroll ? COLORS.success : COLORS.textSecondary,
                fontSize: '11px',
                cursor: 'pointer',
              }}
            >
              {autoScroll ? 'Auto-scroll ON' : 'Auto-scroll OFF'}
            </button>
            <button
              onClick={() => setLogs([])}
              style={{
                padding: '3px 10px',
                background: COLORS.surface2,
                border: `1px solid ${COLORS.border}`,
                borderRadius: '4px',
                color: COLORS.textSecondary,
                fontSize: '11px',
                cursor: 'pointer',
              }}
            >
              Clear
            </button>
          </div>
        </div>
        <div
          ref={logContainerRef}
          onScroll={handleLogScroll}
          style={{
            height: '340px',
            overflowY: 'auto',
            padding: '12px 20px',
            background: '#0a0f1e',
          }}
        >
          {logs.length === 0 ? (
            <div style={{ color: COLORS.textSecondary, fontSize: '13px', fontFamily: 'monospace' }}>
              Waiting for log output...
            </div>
          ) : (
            logs.map((line, i) => <LogLine key={i} line={line} />)
          )}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  );
}

const backBtnStyle = {
  padding: '6px 14px',
  background: 'transparent',
  border: `1px solid ${COLORS.border}`,
  borderRadius: '6px',
  color: COLORS.textSecondary,
  cursor: 'pointer',
  fontSize: '13px',
  fontFamily: 'system-ui, -apple-system, sans-serif',
};
