import React, { useState, useEffect, useRef, useCallback } from 'react';

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

function generateConversationId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

function formatMessageText(text) {
  // Simple markdown-like formatting: **bold**, newlines
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    // Handle newlines
    const lines = part.split('\n');
    return lines.map((line, j) => (
      <React.Fragment key={`${i}-${j}`}>
        {line}
        {j < lines.length - 1 && <br />}
      </React.Fragment>
    ));
  });
}

function LoadingDots() {
  return (
    <div style={{ display: 'flex', gap: '4px', alignItems: 'center', padding: '4px 0' }}>
      {[0, 1, 2].map(i => (
        <div
          key={i}
          style={{
            width: '7px',
            height: '7px',
            borderRadius: '50%',
            background: COLORS.textSecondary,
            animation: `bounce 1.4s infinite ease-in-out`,
            animationDelay: `${i * 0.16}s`,
          }}
        />
      ))}
    </div>
  );
}

function Message({ msg }) {
  const isUser = msg.role === 'user';
  const isAction = msg.type === 'action';
  const isError = msg.type === 'error';
  const isSystem = msg.type === 'system';

  if (isAction) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', margin: '4px 0' }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '6px 14px',
          background: COLORS.surface2,
          borderRadius: '20px',
          fontSize: '12px',
          color: COLORS.textSecondary,
          maxWidth: '80%',
        }}>
          <span>🔧</span>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{msg.content}</span>
        </div>
      </div>
    );
  }

  if (isError || isSystem) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', margin: '4px 0' }}>
        <div style={{
          padding: '8px 14px',
          background: isError ? `${COLORS.error}18` : COLORS.surface2,
          border: `1px solid ${isError ? COLORS.error : COLORS.border}44`,
          borderRadius: '8px',
          fontSize: '13px',
          color: isError ? COLORS.error : COLORS.textSecondary,
          maxWidth: '80%',
          textAlign: 'center',
        }}>
          {isError && '⚠️ '}{msg.content}
        </div>
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex',
      justifyContent: isUser ? 'flex-end' : 'flex-start',
      margin: '4px 0',
      gap: '8px',
      alignItems: 'flex-end',
    }}>
      {!isUser && (
        <div style={{
          width: '28px',
          height: '28px',
          borderRadius: '50%',
          background: `${COLORS.primary}33`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '14px',
          flexShrink: 0,
        }}>
          🤖
        </div>
      )}
      <div style={{
        maxWidth: '75%',
        padding: '10px 14px',
        borderRadius: isUser ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
        background: isUser ? COLORS.primary : COLORS.surface2,
        color: COLORS.text,
        fontSize: '14px',
        lineHeight: '1.6',
        wordBreak: 'break-word',
      }}>
        {msg.streaming ? (
          <span>{formatMessageText(msg.content)}<span style={{ animation: 'blink 1s infinite', opacity: 1 }}>▋</span></span>
        ) : (
          formatMessageText(msg.content)
        )}
      </div>
    </div>
  );
}

export default function Chat() {
  const [messages, setMessages] = useState([
    { id: 'welcome', role: 'assistant', type: 'text', content: 'Bonjour! I\'m the Chef de projet agent. I can help you manage tasks, create issues, review progress, and coordinate work. What would you like to do?' }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId] = useState(() => generateConversationId());
  const [conversationHistory, setConversationHistory] = useState([]);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const esRef = useRef(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || isLoading) return;

    const userMsg = { id: `u-${Date.now()}`, role: 'user', type: 'text', content: text };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setIsLoading(true);

    const newHistory = [...conversationHistory, { role: 'user', content: text }];
    setConversationHistory(newHistory);

    // Close any existing SSE connection
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    const assistantMsgId = `a-${Date.now()}`;
    let assistantContent = '';
    let assistantAdded = false;

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          conversation_id: conversationId,
          conversation_history: newHistory,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const contentType = response.headers.get('content-type') || '';

      if (contentType.includes('text/event-stream')) {
        // SSE streaming via fetch (readable stream)
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        const processLine = (line) => {
          if (!line.startsWith('data:')) return;
          const dataStr = line.slice(5).trim();
          if (!dataStr || dataStr === '[DONE]') return;

          let event;
          try {
            event = JSON.parse(dataStr);
          } catch {
            event = { type: 'text', content: dataStr };
          }

          if (event.type === 'text' || event.type === 'delta') {
            assistantContent += event.content || '';
            if (!assistantAdded) {
              setMessages(prev => [...prev, { id: assistantMsgId, role: 'assistant', type: 'text', content: assistantContent, streaming: true }]);
              assistantAdded = true;
            } else {
              setMessages(prev => prev.map(m => m.id === assistantMsgId ? { ...m, content: assistantContent, streaming: true } : m));
            }
          } else if (event.type === 'action') {
            setMessages(prev => [...prev, { id: `act-${Date.now()}-${Math.random()}`, role: 'assistant', type: 'action', content: event.content || '' }]);
          } else if (event.type === 'error') {
            setMessages(prev => [...prev, { id: `err-${Date.now()}`, role: 'system', type: 'error', content: event.content || 'An error occurred.' }]);
          } else if (event.type === 'done') {
            setMessages(prev => prev.map(m => m.id === assistantMsgId ? { ...m, streaming: false } : m));
            setIsLoading(false);
            if (assistantContent) {
              setConversationHistory(h => [...h, { role: 'assistant', content: assistantContent }]);
            }
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (line.trim()) processLine(line);
          }
        }

        // Finalize
        setMessages(prev => prev.map(m => m.id === assistantMsgId ? { ...m, streaming: false } : m));
        setIsLoading(false);
        if (assistantContent) {
          setConversationHistory(h => [...h, { role: 'assistant', content: assistantContent }]);
        }
      } else {
        // JSON response (non-streaming)
        const json = await response.json();
        const content = json.response || json.message || json.content || JSON.stringify(json);
        setMessages(prev => [...prev, { id: assistantMsgId, role: 'assistant', type: 'text', content }]);
        setConversationHistory(h => [...h, { role: 'assistant', content }]);
        setIsLoading(false);
      }
    } catch (err) {
      setMessages(prev => [...prev, {
        id: `err-${Date.now()}`,
        role: 'system',
        type: 'error',
        content: `Error: ${err.message}`,
      }]);
      setIsLoading(false);
    }
  }, [input, isLoading, conversationId, conversationHistory]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      <style>{`
        @keyframes bounce {
          0%, 80%, 100% { transform: scale(0.8); opacity: 0.5; }
          40% { transform: scale(1.2); opacity: 1; }
        }
        @keyframes blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
      `}</style>

      {/* Header */}
      <div style={{ padding: '0 0 16px 0', flexShrink: 0 }}>
        <h1 style={{ color: COLORS.text, fontSize: '22px', fontWeight: 700, margin: 0 }}>Chat</h1>
        <p style={{ color: COLORS.textSecondary, fontSize: '14px', marginTop: '4px', marginBottom: 0 }}>Converse with the Chef de projet agent</p>
      </div>

      {/* Messages area */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        background: COLORS.surface,
        borderRadius: '10px 10px 0 0',
        border: `1px solid ${COLORS.border}`,
        borderBottom: 'none',
        padding: '16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '2px',
        minHeight: 0,
      }}>
        {messages.map(msg => <Message key={msg.id} msg={msg} />)}
        {isLoading && messages[messages.length - 1]?.role !== 'assistant' && (
          <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-end', margin: '4px 0' }}>
            <div style={{
              width: '28px', height: '28px', borderRadius: '50%',
              background: `${COLORS.primary}33`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '14px', flexShrink: 0
            }}>🤖</div>
            <div style={{ padding: '10px 14px', background: COLORS.surface2, borderRadius: '16px 16px 16px 4px' }}>
              <LoadingDots />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div style={{
        background: COLORS.surface,
        borderRadius: '0 0 10px 10px',
        border: `1px solid ${COLORS.border}`,
        borderTop: `1px solid ${COLORS.border}`,
        padding: '12px 16px',
        display: 'flex',
        gap: '10px',
        alignItems: 'flex-end',
        flexShrink: 0,
      }}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          placeholder="Message the agent... (Ctrl+Enter to send)"
          rows={2}
          style={{
            flex: 1,
            padding: '10px 12px',
            background: COLORS.surface2,
            border: `1px solid ${COLORS.border}`,
            borderRadius: '8px',
            color: COLORS.text,
            fontSize: '14px',
            resize: 'none',
            outline: 'none',
            fontFamily: 'system-ui, -apple-system, sans-serif',
            lineHeight: '1.5',
            opacity: isLoading ? 0.6 : 1,
          }}
        />
        <button
          onClick={sendMessage}
          disabled={!input.trim() || isLoading}
          style={{
            padding: '10px 20px',
            background: COLORS.primary,
            border: 'none',
            borderRadius: '8px',
            color: '#fff',
            fontWeight: 600,
            fontSize: '14px',
            cursor: !input.trim() || isLoading ? 'not-allowed' : 'pointer',
            opacity: !input.trim() || isLoading ? 0.4 : 1,
            flexShrink: 0,
            alignSelf: 'flex-end',
            transition: 'opacity 0.15s',
            fontFamily: 'system-ui, -apple-system, sans-serif',
          }}
        >
          Send
        </button>
      </div>
      <div style={{ color: COLORS.textSecondary, fontSize: '11px', textAlign: 'right', marginTop: '4px' }}>
        Ctrl+Enter to send
      </div>
    </div>
  );
}
