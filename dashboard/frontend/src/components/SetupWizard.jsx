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

const AGENTS = [
  { key: 'supervisor', label: 'Supervisor', description: 'Orchestrates the overall workflow, assigns tasks to agents, and monitors progress.' },
  { key: 'architect', label: 'Architect', description: 'Designs the solution architecture, breaks down requirements into actionable specs.' },
  { key: 'coder', label: 'Coder', description: 'Implements features and writes code based on the architectural specifications.' },
  { key: 'tester', label: 'Tester', description: 'Writes and runs tests, validates implementation, reports issues.' },
  { key: 'reviewer', label: 'Reviewer', description: 'Reviews code quality, suggests improvements, approves or requests changes.' },
];

const PROVIDERS = [
  { key: 'anthropic', label: 'Anthropic', description: 'Claude models (Haiku, Sonnet, Opus)' },
  { key: 'openai', label: 'OpenAI', description: 'GPT-4o, GPT-4 Turbo, and more' },
  { key: 'openrouter', label: 'OpenRouter', description: 'Access 200+ models from any provider' },
];

function StepIndicator({ current, total }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '32px' }}>
      {Array.from({ length: total }).map((_, i) => (
        <React.Fragment key={i}>
          <div style={{
            width: '28px',
            height: '28px',
            borderRadius: '50%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '13px',
            fontWeight: 600,
            background: i < current ? COLORS.success : i === current ? COLORS.primary : COLORS.surface2,
            color: i <= current ? '#fff' : COLORS.textSecondary,
            flexShrink: 0,
          }}>
            {i < current ? '✓' : i + 1}
          </div>
          {i < total - 1 && (
            <div style={{
              flex: 1,
              height: '2px',
              background: i < current ? COLORS.success : COLORS.surface2,
              borderRadius: '1px',
            }} />
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

function estimateCost(modelData, promptTokens = 500, completionTokens = 1000) {
  if (!modelData || !modelData.pricing) return null;
  const { prompt, completion } = modelData.pricing;
  if (prompt == null || completion == null) return null;
  const cost = (parseFloat(prompt) * promptTokens) + (parseFloat(completion) * completionTokens);
  if (isNaN(cost)) return null;
  if (cost < 0.000001) return `< $0.000001`;
  if (cost < 0.01) return `$${cost.toFixed(6)}`;
  return `$${cost.toFixed(4)}`;
}

export default function SetupWizard({ onComplete }) {
  const [step, setStep] = useState(0);
  const [provider, setProvider] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [keyVerified, setKeyVerified] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState(null);
  const [modelMap, setModelMap] = useState({});
  const [allModels, setAllModels] = useState([]);
  const [systemStatus, setSystemStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  const totalSteps = provider === 'openrouter' ? 4 : 3;

  const verifyOpenRouter = async () => {
    setVerifying(true);
    setVerifyResult(null);
    try {
      const res = await fetch('/api/settings/verify-openrouter', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: apiKey }),
      });
      const json = await res.json();
      if (res.ok && json.valid) {
        setKeyVerified(true);
        setVerifyResult({ success: true, message: `Valid key — ${json.model_count ?? json.models ?? 'many'} models available` });
      } else {
        setKeyVerified(false);
        setVerifyResult({ success: false, message: json.error || json.message || 'Invalid API key' });
      }
    } catch (err) {
      setKeyVerified(false);
      setVerifyResult({ success: false, message: 'Verification failed: ' + err.message });
    } finally {
      setVerifying(false);
    }
  };

  const fetchAllModels = useCallback(async () => {
    if (!apiKey || provider !== 'openrouter') return;
    try {
      const res = await fetch(`/api/models/openrouter/filtered?key=${encodeURIComponent(apiKey)}`);
      if (res.ok) {
        const json = await res.json();
        const list = Array.isArray(json) ? json : (json.data || json.models || []);
        setAllModels(list);
      }
    } catch { /* ignore */ }
  }, [apiKey, provider]);

  useEffect(() => {
    if (step === (provider === 'openrouter' ? 2 : 1)) {
      fetchSystemStatus();
    }
  }, [step]);

  useEffect(() => {
    if (provider === 'openrouter' && step === 1 && allModels.length === 0) {
      fetchAllModels();
    }
  }, [step, provider]);

  const fetchSystemStatus = async () => {
    setStatusLoading(true);
    try {
      const res = await fetch('/api/system/status');
      if (res.ok) setSystemStatus(await res.json());
    } catch { /* ignore */ }
    setStatusLoading(false);
  };

  const canProceedStep0 = () => {
    if (!provider) return false;
    if (provider === 'openrouter') return keyVerified;
    return apiKey.trim().length > 0;
  };

  const handleNext = () => {
    if (step < totalSteps - 1) setStep(s => s + 1);
  };

  const handleBack = () => {
    if (step > 0) setStep(s => s - 1);
  };

  const handleLaunch = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const settings = {
        provider,
        api_keys: { [provider]: apiKey },
        models: provider === 'openrouter' ? modelMap : {},
      };
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${res.status}`);
      }
      onComplete();
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const getModelData = (agentKey) => allModels.find(m => m.id === modelMap[agentKey]);

  const wrapStyle = {
    minHeight: '100vh',
    background: COLORS.bg,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '24px',
    fontFamily: 'system-ui, -apple-system, sans-serif',
  };

  const cardStyle = {
    background: COLORS.surface,
    borderRadius: '12px',
    padding: '40px',
    width: '100%',
    maxWidth: '640px',
    border: `1px solid ${COLORS.border}`,
    boxShadow: '0 24px 64px rgba(0,0,0,0.5)',
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
    padding: '10px 12px',
    background: COLORS.surface2,
    border: `1px solid ${COLORS.border}`,
    borderRadius: '6px',
    color: COLORS.text,
    fontSize: '14px',
    outline: 'none',
    boxSizing: 'border-box',
    fontFamily: 'monospace',
  };

  const btnPrimary = {
    padding: '10px 24px',
    background: COLORS.primary,
    border: 'none',
    borderRadius: '6px',
    color: '#fff',
    fontSize: '14px',
    fontWeight: 600,
    cursor: 'pointer',
  };

  const btnSecondary = {
    padding: '10px 24px',
    background: 'transparent',
    border: `1px solid ${COLORS.border}`,
    borderRadius: '6px',
    color: COLORS.textSecondary,
    fontSize: '14px',
    cursor: 'pointer',
  };

  const sectionTitle = {
    fontSize: '22px',
    fontWeight: 700,
    color: COLORS.text,
    marginBottom: '8px',
  };

  const sectionSubtitle = {
    color: COLORS.textSecondary,
    fontSize: '14px',
    marginBottom: '24px',
  };

  // Step 0: Provider selection
  const renderStep0 = () => (
    <div>
      <div style={sectionTitle}>Choose LLM Provider</div>
      <div style={sectionSubtitle}>Select the AI provider you want to use for all agents.</div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '24px' }}>
        {PROVIDERS.map(p => (
          <button
            key={p.key}
            type="button"
            onClick={() => { setProvider(p.key); setKeyVerified(false); setVerifyResult(null); setApiKey(''); }}
            style={{
              padding: '16px 20px',
              background: provider === p.key ? `${COLORS.primary}22` : COLORS.surface2,
              border: `2px solid ${provider === p.key ? COLORS.primary : COLORS.border}`,
              borderRadius: '8px',
              color: COLORS.text,
              cursor: 'pointer',
              textAlign: 'left',
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
              transition: 'all 0.15s',
            }}
          >
            <div style={{
              width: '20px',
              height: '20px',
              borderRadius: '50%',
              border: `2px solid ${provider === p.key ? COLORS.primary : COLORS.border}`,
              background: provider === p.key ? COLORS.primary : 'transparent',
              flexShrink: 0,
            }} />
            <div>
              <div style={{ fontWeight: 600, fontSize: '15px' }}>{p.label}</div>
              <div style={{ color: COLORS.textSecondary, fontSize: '13px', marginTop: '2px' }}>{p.description}</div>
            </div>
          </button>
        ))}
      </div>

      {provider && (
        <div style={{ marginBottom: '16px' }}>
          <label style={labelStyle}>{PROVIDERS.find(p2 => p2.key === provider)?.label} API Key</label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              type="password"
              style={{ ...inputStyle, flex: 1 }}
              value={apiKey}
              onChange={e => { setApiKey(e.target.value); setKeyVerified(false); setVerifyResult(null); }}
              placeholder={provider === 'openrouter' ? 'sk-or-...' : provider === 'anthropic' ? 'sk-ant-...' : 'sk-...'}
            />
            {provider === 'openrouter' && (
              <button
                type="button"
                onClick={verifyOpenRouter}
                disabled={!apiKey.trim() || verifying}
                style={{
                  ...btnPrimary,
                  opacity: (!apiKey.trim() || verifying) ? 0.5 : 1,
                  cursor: (!apiKey.trim() || verifying) ? 'not-allowed' : 'pointer',
                  flexShrink: 0,
                }}
              >
                {verifying ? 'Verifying...' : 'Verify Key'}
              </button>
            )}
          </div>

          {verifyResult && (
            <div style={{
              marginTop: '8px',
              padding: '8px 12px',
              borderRadius: '6px',
              fontSize: '13px',
              background: verifyResult.success ? `${COLORS.success}18` : `${COLORS.error}18`,
              color: verifyResult.success ? COLORS.success : COLORS.error,
              border: `1px solid ${verifyResult.success ? COLORS.success : COLORS.error}44`,
            }}>
              {verifyResult.success ? '✓ ' : '✗ '}{verifyResult.message}
            </div>
          )}
        </div>
      )}
    </div>
  );

  // Step 1: Model Configuration (OpenRouter only)
  const renderStepModels = () => (
    <div>
      <div style={sectionTitle}>Configure Agent Models</div>
      <div style={sectionSubtitle}>Choose a model for each agent role. Costs are estimated for 500 input + 1000 output tokens.</div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        {AGENTS.map(agent => {
          const modelData = getModelData(agent.key);
          const cost = estimateCost(modelData);
          return (
            <div key={agent.key} style={{ background: COLORS.surface2, borderRadius: '8px', padding: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '8px' }}>
                <div>
                  <div style={{ color: COLORS.text, fontWeight: 600, fontSize: '14px' }}>{agent.label}</div>
                  <div style={{ color: COLORS.textSecondary, fontSize: '12px', marginTop: '2px', maxWidth: '380px' }}>{agent.description}</div>
                </div>
                {cost && (
                  <div style={{ color: COLORS.textSecondary, fontSize: '12px', flexShrink: 0, marginLeft: '8px', textAlign: 'right' }}>
                    <div style={{ color: COLORS.warning, fontWeight: 600 }}>{cost}</div>
                    <div style={{ fontSize: '10px' }}>est/task</div>
                  </div>
                )}
              </div>
              <ModelSelector
                value={modelMap[agent.key] || ''}
                onChange={(id) => setModelMap(m => ({ ...m, [agent.key]: id }))}
                agentName={agent.label}
                apiKey={apiKey}
                suggestedOnly={false}
              />
            </div>
          );
        })}
      </div>
    </div>
  );

  // Step: Stack Status
  const renderStepStatus = () => {
    const services = systemStatus ? (Array.isArray(systemStatus.services) ? systemStatus.services : Object.entries(systemStatus.services || {}).map(([k, v]) => ({ name: k, ...v }))) : [];
    const config = systemStatus?.config || {};

    return (
      <div>
        <div style={sectionTitle}>System Status</div>
        <div style={sectionSubtitle}>Verifying all required services are online.</div>

        {statusLoading && (
          <div style={{ color: COLORS.textSecondary, fontSize: '14px', padding: '20px 0' }}>Checking services...</div>
        )}

        {!statusLoading && services.length === 0 && (
          <div style={{ color: COLORS.textSecondary, fontSize: '14px', padding: '20px 0' }}>No service data available.</div>
        )}

        {services.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '24px' }}>
            {services.map((svc, i) => {
              const status = svc.status || svc.state || 'unknown';
              const isOk = status === 'ok' || status === 'healthy' || status === 'online' || status === 'up';
              const isWarn = status === 'degraded' || status === 'warning' || status === 'slow';
              const dotColor = isOk ? COLORS.success : isWarn ? COLORS.warning : COLORS.error;
              return (
                <div key={svc.name || i} style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                  padding: '12px 16px',
                  background: COLORS.surface2,
                  borderRadius: '8px',
                }}>
                  <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: dotColor, flexShrink: 0 }} />
                  <div style={{ flex: 1, color: COLORS.text, fontSize: '14px', fontWeight: 500 }}>{svc.name}</div>
                  {svc.latency != null && (
                    <div style={{ color: COLORS.textSecondary, fontSize: '12px' }}>{svc.latency}ms</div>
                  )}
                  <div style={{ color: isOk ? COLORS.success : isWarn ? COLORS.warning : COLORS.error, fontSize: '12px', fontWeight: 600 }}>
                    {status}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {Object.keys(config).length > 0 && (
          <div style={{ background: COLORS.surface2, borderRadius: '8px', padding: '16px' }}>
            <div style={{ color: COLORS.textSecondary, fontSize: '12px', fontWeight: 600, marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Configuration</div>
            {Object.entries(config).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', padding: '4px 0', borderBottom: `1px solid ${COLORS.border}22` }}>
                <span style={{ color: COLORS.textSecondary }}>{k}</span>
                <span style={{ color: COLORS.text }}>{String(v)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  // Final step: Confirm & Launch
  const renderStepConfirm = () => (
    <div>
      <div style={sectionTitle}>Ready to Launch</div>
      <div style={sectionSubtitle}>Review your configuration and launch AgentForge.</div>

      <div style={{ background: COLORS.surface2, borderRadius: '8px', padding: '20px', marginBottom: '24px' }}>
        <div style={{ color: COLORS.textSecondary, fontSize: '12px', fontWeight: 600, marginBottom: '14px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Configuration Summary</div>

        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '14px', padding: '6px 0', borderBottom: `1px solid ${COLORS.border}` }}>
          <span style={{ color: COLORS.textSecondary }}>Provider</span>
          <span style={{ color: COLORS.text, fontWeight: 600, textTransform: 'capitalize' }}>{provider}</span>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '14px', padding: '6px 0', borderBottom: `1px solid ${COLORS.border}` }}>
          <span style={{ color: COLORS.textSecondary }}>API Key</span>
          <span style={{ color: COLORS.text, fontFamily: 'monospace' }}>{apiKey.slice(0, 8)}{'•'.repeat(Math.max(0, apiKey.length - 8))}</span>
        </div>

        {provider === 'openrouter' && AGENTS.map(agent => {
          const modelId = modelMap[agent.key];
          const modelData = allModels.find(m => m.id === modelId);
          return (
            <div key={agent.key} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', padding: '6px 0', borderBottom: `1px solid ${COLORS.border}22` }}>
              <span style={{ color: COLORS.textSecondary }}>{agent.label}</span>
              <span style={{ color: modelId ? COLORS.text : COLORS.textSecondary, maxWidth: '260px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {modelData?.name || modelId || 'Not set'}
              </span>
            </div>
          );
        })}
      </div>

      {saveError && (
        <div style={{ padding: '12px', background: `${COLORS.error}18`, border: `1px solid ${COLORS.error}44`, borderRadius: '6px', color: COLORS.error, fontSize: '13px', marginBottom: '16px' }}>
          {saveError}
        </div>
      )}

      <button
        type="button"
        onClick={handleLaunch}
        disabled={saving}
        style={{ ...btnPrimary, width: '100%', opacity: saving ? 0.6 : 1, cursor: saving ? 'not-allowed' : 'pointer', fontSize: '16px', padding: '14px' }}
      >
        {saving ? 'Launching...' : 'Launch AgentForge'}
      </button>
    </div>
  );

  const stepOrder = provider === 'openrouter'
    ? [renderStep0, renderStepModels, renderStepStatus, renderStepConfirm]
    : [renderStep0, renderStepStatus, renderStepConfirm];

  return (
    <div style={wrapStyle}>
      <div style={cardStyle}>
        <div style={{ marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span style={{ fontSize: '24px' }}>⚡</span>
          <span style={{ color: COLORS.primary, fontWeight: 700, fontSize: '20px' }}>AgentForge</span>
          <span style={{ color: COLORS.textSecondary, fontSize: '14px' }}>Setup</span>
        </div>

        <StepIndicator current={step} total={totalSteps} />

        {stepOrder[step]?.()}

        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '32px' }}>
          <button
            type="button"
            onClick={handleBack}
            disabled={step === 0}
            style={{ ...btnSecondary, opacity: step === 0 ? 0 : 1, cursor: step === 0 ? 'default' : 'pointer' }}
          >
            Back
          </button>
          {step < totalSteps - 1 && (
            <button
              type="button"
              onClick={handleNext}
              disabled={step === 0 && !canProceedStep0()}
              style={{
                ...btnPrimary,
                opacity: (step === 0 && !canProceedStep0()) ? 0.4 : 1,
                cursor: (step === 0 && !canProceedStep0()) ? 'not-allowed' : 'pointer',
              }}
            >
              Next
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
