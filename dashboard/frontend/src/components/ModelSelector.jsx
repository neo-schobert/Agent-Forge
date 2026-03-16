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

function formatPrice(price) {
  if (price == null) return 'N/A';
  const num = parseFloat(price);
  if (isNaN(num)) return 'N/A';
  // price is per token, convert to per million
  const perM = num * 1_000_000;
  if (perM < 0.01) return `$${(perM).toFixed(4)}/M`;
  if (perM < 1) return `$${perM.toFixed(3)}/M`;
  return `$${perM.toFixed(2)}/M`;
}

function formatContext(ctx) {
  if (!ctx) return 'N/A';
  if (ctx >= 1_000_000) return `${(ctx / 1_000_000).toFixed(1)}M ctx`;
  if (ctx >= 1000) return `${Math.round(ctx / 1000)}K ctx`;
  return `${ctx} ctx`;
}

export default function ModelSelector({ value, onChange, agentName, apiKey, suggestedOnly = false }) {
  const [models, setModels] = useState([]);
  const [filteredModels, setFilteredModels] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showAll, setShowAll] = useState(!suggestedOnly);
  const [search, setSearch] = useState('');
  const [isOpen, setIsOpen] = useState(false);

  const fetchModels = useCallback(async (all) => {
    if (!apiKey) return;
    setLoading(true);
    setError(null);
    try {
      const endpoint = all
        ? `/api/models/openrouter?key=${encodeURIComponent(apiKey)}`
        : `/api/models/openrouter/filtered?key=${encodeURIComponent(apiKey)}`;
      const res = await fetch(endpoint);
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      const json = await res.json();
      const list = Array.isArray(json) ? json : (json.data || json.models || []);
      setModels(list);
    } catch (err) {
      setError('Cannot load models from OpenRouter: ' + (err.message || 'Unknown error'));
      setModels([]);
    } finally {
      setLoading(false);
    }
  }, [apiKey]);

  useEffect(() => {
    fetchModels(showAll);
  }, [fetchModels, showAll]);

  useEffect(() => {
    if (!search.trim()) {
      setFilteredModels(models);
    } else {
      const q = search.toLowerCase();
      setFilteredModels(models.filter(m => {
        const name = (m.name || m.id || '').toLowerCase();
        const id = (m.id || '').toLowerCase();
        return name.includes(q) || id.includes(q);
      }));
    }
  }, [models, search]);

  const selectedModel = models.find(m => m.id === value);

  const containerStyle = {
    position: 'relative',
    fontFamily: 'system-ui, -apple-system, sans-serif',
  };

  const triggerStyle = {
    width: '100%',
    padding: '8px 12px',
    background: COLORS.surface2,
    border: `1px solid ${COLORS.border}`,
    borderRadius: '6px',
    color: COLORS.text,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    fontSize: '14px',
    boxSizing: 'border-box',
  };

  const dropdownStyle = {
    position: 'absolute',
    top: '100%',
    left: 0,
    right: 0,
    zIndex: 1000,
    background: COLORS.surface,
    border: `1px solid ${COLORS.border}`,
    borderRadius: '6px',
    marginTop: '4px',
    boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
    maxHeight: '420px',
    display: 'flex',
    flexDirection: 'column',
  };

  const searchStyle = {
    padding: '8px 12px',
    background: COLORS.surface2,
    border: 'none',
    borderBottom: `1px solid ${COLORS.border}`,
    color: COLORS.text,
    fontSize: '13px',
    outline: 'none',
    borderRadius: '6px 6px 0 0',
  };

  const modelListStyle = {
    overflowY: 'auto',
    flex: 1,
  };

  const modelRowStyle = (isSelected) => ({
    padding: '10px 12px',
    cursor: 'pointer',
    background: isSelected ? `${COLORS.primary}22` : 'transparent',
    borderBottom: `1px solid ${COLORS.border}22`,
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
    transition: 'background 0.1s',
  });

  const toggleRowStyle = {
    padding: '8px 12px',
    borderTop: `1px solid ${COLORS.border}`,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  };

  return (
    <div style={containerStyle}>
      <button
        style={triggerStyle}
        onClick={() => setIsOpen(o => !o)}
        type="button"
      >
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, textAlign: 'left' }}>
          {selectedModel
            ? (selectedModel.name || selectedModel.id)
            : (value ? value : `Select model for ${agentName || 'agent'}...`)}
        </span>
        <span style={{ marginLeft: '8px', color: COLORS.textSecondary, flexShrink: 0 }}>
          {isOpen ? '▲' : '▼'}
        </span>
      </button>

      {isOpen && (
        <div style={dropdownStyle}>
          <input
            style={searchStyle}
            placeholder="Search models..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            autoFocus
          />

          {loading && (
            <div style={{ padding: '16px', textAlign: 'center', color: COLORS.textSecondary, fontSize: '13px' }}>
              Loading models...
            </div>
          )}

          {error && (
            <div style={{ padding: '12px', color: COLORS.error, fontSize: '13px', background: `${COLORS.error}11`, margin: '8px', borderRadius: '4px', border: `1px solid ${COLORS.error}44` }}>
              {error}
            </div>
          )}

          {!loading && !error && (
            <div style={modelListStyle}>
              {filteredModels.length === 0 && (
                <div style={{ padding: '16px', textAlign: 'center', color: COLORS.textSecondary, fontSize: '13px' }}>
                  {search ? 'No models match your search.' : 'No models available.'}
                </div>
              )}
              {filteredModels.map(model => {
                const isSelected = model.id === value;
                const pricing = model.pricing || {};
                const promptPrice = pricing.prompt;
                const completionPrice = pricing.completion;
                const contextLen = model.context_length || model.top_provider?.context_length;
                return (
                  <div
                    key={model.id}
                    style={modelRowStyle(isSelected)}
                    onClick={() => {
                      onChange(model.id);
                      setIsOpen(false);
                    }}
                    onMouseEnter={e => {
                      if (!isSelected) e.currentTarget.style.background = `${COLORS.surface2}`;
                    }}
                    onMouseLeave={e => {
                      if (!isSelected) e.currentTarget.style.background = 'transparent';
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', overflow: 'hidden' }}>
                        {isSelected && (
                          <span style={{ color: COLORS.success, flexShrink: 0, fontSize: '12px' }}>✓</span>
                        )}
                        <span style={{ color: COLORS.text, fontSize: '13px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {model.name || model.id}
                        </span>
                      </div>
                      <a
                        href={`https://openrouter.ai/models/${model.id}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={e => e.stopPropagation()}
                        style={{ color: COLORS.primary, fontSize: '11px', flexShrink: 0, textDecoration: 'none' }}
                        title="View on OpenRouter"
                      >
                        ↗
                      </a>
                    </div>
                    <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                      {contextLen && (
                        <span style={{ color: COLORS.textSecondary, fontSize: '11px' }}>
                          {formatContext(contextLen)}
                        </span>
                      )}
                      {promptPrice != null && (
                        <span style={{ color: COLORS.textSecondary, fontSize: '11px' }}>
                          In: {formatPrice(promptPrice)}
                        </span>
                      )}
                      {completionPrice != null && (
                        <span style={{ color: COLORS.textSecondary, fontSize: '11px' }}>
                          Out: {formatPrice(completionPrice)}
                        </span>
                      )}
                    </div>
                    {model.id && (
                      <div style={{ color: COLORS.textSecondary, fontSize: '10px', opacity: 0.7, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {model.id}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div style={toggleRowStyle}>
            <span style={{ color: COLORS.textSecondary, fontSize: '12px' }}>
              {filteredModels.length} model{filteredModels.length !== 1 ? 's' : ''}
            </span>
            <button
              type="button"
              onClick={() => setShowAll(v => !v)}
              style={{
                background: 'none',
                border: `1px solid ${COLORS.border}`,
                borderRadius: '4px',
                color: COLORS.primary,
                cursor: 'pointer',
                fontSize: '12px',
                padding: '4px 8px',
              }}
            >
              {showAll ? 'Show suggested only' : 'Show all models'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
