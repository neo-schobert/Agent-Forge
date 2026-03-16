import React, { useState, useEffect, useCallback } from 'react';
import ModelSelector from './ModelSelector.jsx';

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

const PROVIDERS = [
  { key: 'anthropic',  label: 'Anthropic'  },
  { key: 'openai',     label: 'OpenAI'     },
  { key: 'openrouter', label: 'OpenRouter' },
];

const AGENTS = [
  { key: 'supervisor', label: 'Supervisor' },
  { key: 'architect',  label: 'Architect'  },
  { key: 'coder',      label: 'Coder'      },
  { key: 'tester',     label: 'Tester'     },
  { key: 'reviewer',   label: 'Reviewer'   },
];

const KEY_PLACEHOLDERS = {
  anthropic:  'sk-ant-...',
  openai:     'sk-...',
  openrouter: 'sk-or-...',
};

function Toast({ toasts }) {
  return (
    <div style={{ position: 'fixed', bottom: '24px', right: '24px', zIndex: 9999, display: 'flex', flexDirection: 'column', gap: '8px', pointerEvents: 'none' }}>
      {toasts.map(t => (
        <div key={t.id} style={{
          padding: '12px 18px',
          borderRadius: '8px',
          fontSize: '14px',
          fontWeight: 500,
          background: t.type === 'success' ? '#1a3a2a' : t.type === 'error' ? '#3a1a1a' : COLORS.surface2,
          border: `1px solid ${t.type === 'success' ? COLORS.success : t.type === 'error' ? COLORS.error : COLORS.border}66`,
          color: t.type === 'success' ? COLORS.success : t.type === 'error' ? COLORS.error : COLORS.text,
          boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
          minWidth: '220px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}>
          {t.type === 'success' ? '✓' : t.type === 'error' ? '✗' : 'ℹ'} {t.message}
        </div>
      ))}
    </div>
  );
}

export default function Settings() {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // Editable state
  const [provider, setProvider] = useState('anthropic');
  const [apiKeys, setApiKeys] = useState({ anthropic: '', openai: '', openrouter: '' });
  const [modelMap, setModelMap] = useState({});

  // Per-key state
  const [keyEditing, setKeyEditing] = useState({});
  const [keyTesting, setKeyTesting] = useState({});
  const [keyResults, setKeyResults] = useState({});

  const [toasts, setToasts] = useState([]);

  const addToast = useCallback((message, type = 'info', duration = 3500) => {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), duration);
  }, []);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await fetch('/api/settings');
      if (res.ok) {
        const data = await res.json();
        setSettings(data);
        setProvider(data.provider || 'anthropic');
        setApiKeys({
          anthropic:  data.api_keys?.anthropic  || '',
          openai:     data.api_keys?.openai     || '',
          openrouter: data.api_keys?.openrouter || '',
        });
        setModelMap(data.models || {});
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchSettings(); }, [fetchSettings]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = {
        provider,
        api_keys: apiKeys,
        models: modelMap,
      };
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${res.status}`);
      }
      addToast('Settings saved successfully.', 'success');
    } catch (err) {
      addToast(`Failed to save: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnection = async (providerKey) => {
    const key = apiKeys[providerKey];
    if (!key) {
      addToast('Enter an API key first.', 'error');
      return;
    }
    setKeyTesting(t => ({ ...t, [providerKey]: true }));
    setKeyResults(r => ({ ...r, [providerKey]: null }));
    try {
      if (providerKey === 'openrouter') {
        const res = await fetch('/api/settings/verify-openrouter', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key }),
        });
        const json = await res.json();
        if (res.ok && json.valid) {
          setKeyResults(r => ({ ...r, [providerKey]: { success: true, message: `Valid — ${json.model_count ?? json.models ?? '?'} models` } }));
        } else {
          setKeyResults(r => ({ ...r, [providerKey]: { success: false, message: json.error || 'Invalid key' } }));
        }
      } else {
        // Generic test endpoint
        const res = await fetch('/api/settings/verify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: providerKey, key }),
        });
        const json = await res.json().catch(() => ({}));
        if (res.ok && (json.valid || json.ok)) {
          setKeyResults(r => ({ ...r, [providerKey]: { success: true, message: 'Connection successful' } }));
        } else {
          setKeyResults(r => ({ ...r, [providerKey]: { success: false, message: json.error || 'Connection failed' } }));
        }
      }
    } catch (err) {
      setKeyResults(r => ({ ...r, [providerKey]: { success: false, message: err.message } }));
    } finally {
      setKeyTesting(t => ({ ...t, [providerKey]: false }));
    }
  };

  const sectionCard = {
    background: COLORS.surface,
    borderRadius: '10px',
    padding: '20px',
    border: `1px solid ${COLORS.border}`,
    marginBottom: '20px',
  };

  const sectionTitle = {
    color: COLORS.text,
    fontSize: '16px',
    fontWeight: 700,
    marginBottom: '16px',
  };

  const labelStyle = {
    display: 'block',
    color: COLORS.textSecondary,
    fontSize: '13px',
    marginBottom: '6px',
    fontWeight: 500,
  };

  const inputStyle = {
    width: '100%',
    padding: '9px 12px',
    background: COLORS.surface2,
    border: `1px solid ${COLORS.border}`,
    borderRadius: '6px',
    color: COLORS.text,
    fontSize: '14px',
    outline: 'none',
    boxSizing: 'border-box',
    fontFamily: 'monospace',
  };

  const selectStyle = {
    padding: '9px 12px',
    background: COLORS.surface2,
    border: `1px solid ${COLORS.border}`,
    borderRadius: '6px',
    color: COLORS.text,
    fontSize: '14px',
    outline: 'none',
    cursor: 'pointer',
  };

  const btnSmall = {
    padding: '6px 14px',
    border: 'none',
    borderRadius: '6px',
    fontSize: '13px',
    fontWeight: 600,
    cursor: 'pointer',
    flexShrink: 0,
  };

  if (loading) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: COLORS.textSecondary, fontFamily: 'system-ui, -apple-system, sans-serif' }}>
        Loading settings...
      </div>
    );
  }

  return (
    <div style={{ fontFamily: 'system-ui, -apple-system, sans-serif', maxWidth: '700px' }}>
      <Toast toasts={toasts} />

      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ color: COLORS.text, fontSize: '22px', fontWeight: 700, margin: 0 }}>Settings</h1>
        <p style={{ color: COLORS.textSecondary, fontSize: '14px', marginTop: '4px', marginBottom: 0 }}>Configure providers, models, and API keys</p>
      </div>

      {/* Provider */}
      <div style={sectionCard}>
        <div style={sectionTitle}>LLM Provider</div>
        <label style={labelStyle}>Active Provider</label>
        <select
          value={provider}
          onChange={e => setProvider(e.target.value)}
          style={selectStyle}
        >
          {PROVIDERS.map(p => (
            <option key={p.key} value={p.key}>{p.label}</option>
          ))}
        </select>
      </div>

      {/* API Keys */}
      <div style={sectionCard}>
        <div style={sectionTitle}>API Keys</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {PROVIDERS.map(p => (
            <div key={p.key}>
              <label style={labelStyle}>{p.label} API Key</label>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <input
                  type={keyEditing[p.key] ? 'text' : 'password'}
                  value={apiKeys[p.key]}
                  onChange={e => setApiKeys(k => ({ ...k, [p.key]: e.target.value }))}
                  placeholder={KEY_PLACEHOLDERS[p.key]}
                  style={{ ...inputStyle, flex: 1 }}
                />
                <button
                  type="button"
                  onClick={() => setKeyEditing(e => ({ ...e, [p.key]: !e[p.key] }))}
                  style={{ ...btnSmall, background: COLORS.surface2, color: COLORS.textSecondary }}
                >
                  {keyEditing[p.key] ? 'Hide' : 'Show'}
                </button>
                <button
                  type="button"
                  onClick={() => handleTestConnection(p.key)}
                  disabled={!apiKeys[p.key] || keyTesting[p.key]}
                  style={{
                    ...btnSmall,
                    background: COLORS.primary,
                    color: '#fff',
                    opacity: (!apiKeys[p.key] || keyTesting[p.key]) ? 0.5 : 1,
                    cursor: (!apiKeys[p.key] || keyTesting[p.key]) ? 'not-allowed' : 'pointer',
                  }}
                >
                  {keyTesting[p.key] ? 'Testing...' : 'Test'}
                </button>
              </div>
              {keyResults[p.key] && (
                <div style={{
                  marginTop: '6px',
                  fontSize: '12px',
                  color: keyResults[p.key].success ? COLORS.success : COLORS.error,
                }}>
                  {keyResults[p.key].success ? '✓ ' : '✗ '}{keyResults[p.key].message}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Model Configuration (OpenRouter only) */}
      {provider === 'openrouter' && (
        <div style={sectionCard}>
          <div style={sectionTitle}>Model Configuration</div>
          <p style={{ color: COLORS.textSecondary, fontSize: '13px', marginTop: '-8px', marginBottom: '16px' }}>
            Assign a model to each agent role.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {AGENTS.map(agent => (
              <div key={agent.key}>
                <label style={labelStyle}>{agent.label}</label>
                <ModelSelector
                  value={modelMap[agent.key] || ''}
                  onChange={(id) => setModelMap(m => ({ ...m, [agent.key]: id }))}
                  agentName={agent.label}
                  apiKey={apiKeys.openrouter}
                  suggestedOnly={false}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Save button */}
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          onClick={handleSave}
          disabled={saving}
          style={{
            padding: '11px 28px',
            background: COLORS.primary,
            border: 'none',
            borderRadius: '8px',
            color: '#fff',
            fontSize: '15px',
            fontWeight: 700,
            cursor: saving ? 'not-allowed' : 'pointer',
            opacity: saving ? 0.6 : 1,
            fontFamily: 'system-ui, -apple-system, sans-serif',
          }}
        >
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );
}
