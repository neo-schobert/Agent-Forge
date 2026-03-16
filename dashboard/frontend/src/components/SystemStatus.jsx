import React, { useState, useEffect, useCallback, useRef } from 'react';
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

const EVENT_ICONS = {
  webhook_received: '🔗',
  task_started: '▶️',
  task_completed: '✅',
  task_failed: '❌',
  pr_created: '🔀',
  container_spawned: '📦',
};

function timeAgo(timestamp) {
  const now = Date.now();
  const ts = typeof timestamp === 'number' ? timestamp : new Date(timestamp).getTime();
  const diff = Math.floor((now - ts) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function ServiceDot({ status, latency }) {
  const isOk = status === 'ok' || status === 'healthy' || status === 'online' || status === 'up';
  const isWarn = status === 'degraded' || status === 'warning' || status === 'slow';
  const color = isOk ? COLORS.success : isWarn ? COLORS.warning : COLORS.error;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
      <div style={{
        width: '10px',
        height: '10px',
        borderRadius: '50%',
        background: color,
        boxShadow: isOk ? `0 0 6px ${color}88` : undefined,
        flexShrink: 0,
      }} />
      <span style={{ color: isOk ? COLORS.success : isWarn ? COLORS.warning : COLORS.error, fontSize: '12px', fontWeight: 500 }}>
        {status || 'unknown'}
      </span>
      {latency != null && (
        <span style={{ color: COLORS.textSecondary, fontSize: '11px' }}>{latency}ms</span>
      )}
    </div>
  );
}

export default function SystemStatus() {
  const [systemStatus, setSystemStatus] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [config, setConfig] = useState(null);
  const [activity, setActivity] = useState([]);
  const maxActivity = 10;

  const { lastMessage } = useWebSocket('/ws');

  useEffect(() => {
    if (!lastMessage) return;
    const msg = lastMessage.data;
    if (!msg || typeof msg !== 'object') return;
    const eventTypes = ['webhook_received', 'task_started', 'task_completed', 'task_failed', 'pr_created', 'container_spawned'];
    if (msg.type && eventTypes.includes(msg.type)) {
      setActivity(prev => {
        const next = [{ ...msg, _id: `${Date.now()}-${Math.random()}`, _ts: Date.now() }, ...prev];
        return next.slice(0, maxActivity);
      });
    }
  }, [lastMessage]);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/system/status');
      if (res.ok) setSystemStatus(await res.json());
    } catch { /* ignore */ }
  }, []);

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch('/api/tasks');
      if (res.ok) {
        const data = await res.json();
        setTasks(Array.isArray(data) ? data : (data.tasks || []));
      }
    } catch { /* ignore */ }
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch('/api/system/config');
      if (res.ok) setConfig(await res.json());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchTasks();
    fetchConfig();
    const statusInterval = setInterval(fetchStatus, 30000);
    const taskInterval = setInterval(fetchTasks, 15000);
    return () => {
      clearInterval(statusInterval);
      clearInterval(taskInterval);
    };
  }, []);

  const services = systemStatus
    ? (Array.isArray(systemStatus.services)
        ? systemStatus.services
        : Object.entries(systemStatus.services || {}).map(([name, v]) => ({ name, ...v })))
    : [];

  const activeCount = tasks.filter(t => t.status === 'running').length;
  const today = new Date().toDateString();
  const completedToday = tasks.filter(t => {
    if (t.status !== 'done' && t.status !== 'completed') return false;
    if (!t.completed_at && !t.updated_at) return true;
    return new Date(t.completed_at || t.updated_at).toDateString() === today;
  }).length;
  const doneCount = tasks.filter(t => t.status === 'done' || t.status === 'completed').length;
  const failedCount = tasks.filter(t => t.status === 'failed').length;
  const totalDone = doneCount + failedCount;
  const successRate = totalDone > 0 ? Math.round((doneCount / totalDone) * 100) : null;

  const AGENT_LABELS = ['supervisor', 'architect', 'coder', 'tester', 'reviewer'];

  const cardStyle = {
    background: COLORS.surface,
    borderRadius: '10px',
    padding: '20px',
    border: `1px solid ${COLORS.border}`,
  };

  const sectionTitle = {
    color: COLORS.textSecondary,
    fontSize: '12px',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    marginBottom: '14px',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      {/* Page title */}
      <div>
        <h1 style={{ color: COLORS.text, fontSize: '22px', fontWeight: 700, margin: 0 }}>Dashboard</h1>
        <p style={{ color: COLORS.textSecondary, fontSize: '14px', marginTop: '4px', marginBottom: 0 }}>System overview and real-time activity</p>
      </div>

      {/* Services row */}
      <div style={cardStyle}>
        <div style={sectionTitle}>Service Status</div>
        {services.length === 0 ? (
          <div style={{ color: COLORS.textSecondary, fontSize: '14px' }}>Fetching service status...</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '12px' }}>
            {services.map((svc, i) => (
              <div key={svc.name || i} style={{
                background: COLORS.surface2,
                borderRadius: '8px',
                padding: '12px 16px',
                display: 'flex',
                flexDirection: 'column',
                gap: '6px',
              }}>
                <div style={{ color: COLORS.text, fontSize: '14px', fontWeight: 600 }}>{svc.name}</div>
                <ServiceDot status={svc.status || svc.state} latency={svc.latency} />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
        {[
          { label: 'Active Tasks', value: activeCount, color: COLORS.primary },
          { label: 'Completed Today', value: completedToday, color: COLORS.success },
          { label: 'Success Rate', value: successRate != null ? `${successRate}%` : '—', color: successRate != null && successRate >= 80 ? COLORS.success : successRate != null ? COLORS.warning : COLORS.textSecondary },
        ].map(stat => (
          <div key={stat.label} style={{ ...cardStyle, textAlign: 'center' }}>
            <div style={{ fontSize: '32px', fontWeight: 700, color: stat.color }}>{stat.value}</div>
            <div style={{ color: COLORS.textSecondary, fontSize: '13px', marginTop: '4px' }}>{stat.label}</div>
          </div>
        ))}
      </div>

      {/* Model Configuration */}
      <div style={cardStyle}>
        <div style={sectionTitle}>Model Configuration</div>
        {!config ? (
          <div style={{ color: COLORS.textSecondary, fontSize: '14px' }}>Loading configuration...</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {(() => {
              const provider = config.provider || '';
              const models = config.models || {};
              return AGENT_LABELS.map(agentKey => {
                const modelId = models[agentKey] || config[agentKey] || '';
                return (
                  <div key={agentKey} style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 14px',
                    background: COLORS.surface2,
                    borderRadius: '8px',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{ color: COLORS.textSecondary, fontSize: '13px', minWidth: '80px', textTransform: 'capitalize' }}>{agentKey}</span>
                      <span style={{
                        padding: '2px 8px',
                        borderRadius: '4px',
                        fontSize: '11px',
                        fontWeight: 600,
                        background: provider === 'openrouter' ? `${COLORS.warning}22` : provider === 'anthropic' ? `${COLORS.primary}22` : `${COLORS.success}22`,
                        color: provider === 'openrouter' ? COLORS.warning : provider === 'anthropic' ? COLORS.primary : COLORS.success,
                        textTransform: 'capitalize',
                      }}>
                        {provider || 'N/A'}
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ color: COLORS.text, fontSize: '13px', maxWidth: '260px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {modelId || 'Not configured'}
                      </span>
                      {provider === 'openrouter' && modelId && (
                        <a
                          href={`https://openrouter.ai/models/${modelId}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ color: COLORS.primary, fontSize: '13px', textDecoration: 'none' }}
                          title="View on OpenRouter"
                        >
                          ↗
                        </a>
                      )}
                    </div>
                  </div>
                );
              });
            })()}
          </div>
        )}
      </div>

      {/* Activity Feed */}
      <div style={cardStyle}>
        <div style={sectionTitle}>Recent Activity</div>
        {activity.length === 0 ? (
          <div style={{ color: COLORS.textSecondary, fontSize: '14px', padding: '20px 0', textAlign: 'center' }}>
            Waiting for events...
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {activity.map(event => (
              <div key={event._id} style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '10px',
                padding: '10px 12px',
                background: COLORS.surface2,
                borderRadius: '6px',
                borderLeft: `3px solid ${
                  event.type === 'task_completed' ? COLORS.success :
                  event.type === 'task_failed' ? COLORS.error :
                  event.type === 'task_started' ? COLORS.primary :
                  COLORS.border
                }`,
              }}>
                <span style={{ fontSize: '16px', flexShrink: 0 }}>{EVENT_ICONS[event.type] || '📌'}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: COLORS.text, fontSize: '13px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {event.message || event.content || event.type}
                  </div>
                  {event.details && (
                    <div style={{ color: COLORS.textSecondary, fontSize: '12px', marginTop: '2px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {event.details}
                    </div>
                  )}
                </div>
                <span style={{ color: COLORS.textSecondary, fontSize: '11px', flexShrink: 0 }}>
                  {timeAgo(event._ts)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
