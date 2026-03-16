import React, { useState, useEffect } from 'react';
import SetupWizard from './components/SetupWizard.jsx';
import SystemStatus from './components/SystemStatus.jsx';
import TaskList from './components/TaskList.jsx';
import TaskDetail from './components/TaskDetail.jsx';
import PRViewer from './components/PRViewer.jsx';
import Chat from './components/Chat.jsx';
import Settings from './components/Settings.jsx';

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

const NAV_ITEMS = [
  { key: 'dashboard',  label: 'Dashboard',      icon: '🏠' },
  { key: 'tasks',      label: 'Tasks',           icon: '📋' },
  { key: 'prs',        label: 'Pull Requests',   icon: '🔀' },
  { key: 'chat',       label: 'Chat',            icon: '💬' },
  { key: 'settings',   label: 'Settings',        icon: '⚙️' },
];

const VERSION = '1.0.0';

function Sidebar({ currentView, onNavigate }) {
  return (
    <div style={{
      width: '220px',
      minWidth: '220px',
      background: COLORS.surface,
      borderRight: `1px solid ${COLORS.border}`,
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      position: 'fixed',
      left: 0,
      top: 0,
      zIndex: 100,
    }}>
      {/* Logo */}
      <div style={{ padding: '20px 20px 16px', borderBottom: `1px solid ${COLORS.border}` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '22px' }}>⚡</span>
          <div>
            <div style={{ color: COLORS.text, fontWeight: 700, fontSize: '16px', lineHeight: 1 }}>AgentForge</div>
            <div style={{ color: COLORS.textSecondary, fontSize: '11px', marginTop: '2px' }}>v{VERSION}</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, padding: '10px 10px', overflowY: 'auto' }}>
        {NAV_ITEMS.map(item => {
          const isActive = currentView === item.key || (currentView === 'task-detail' && item.key === 'tasks');
          return (
            <button
              key={item.key}
              onClick={() => onNavigate(item.key)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                width: '100%',
                padding: '9px 12px',
                background: isActive ? `${COLORS.primary}22` : 'transparent',
                border: isActive ? `1px solid ${COLORS.primary}44` : '1px solid transparent',
                borderRadius: '7px',
                color: isActive ? COLORS.primary : COLORS.textSecondary,
                cursor: 'pointer',
                fontSize: '14px',
                fontWeight: isActive ? 600 : 400,
                textAlign: 'left',
                marginBottom: '2px',
                transition: 'all 0.15s',
                fontFamily: 'system-ui, -apple-system, sans-serif',
              }}
              onMouseEnter={e => {
                if (!isActive) {
                  e.currentTarget.style.background = `${COLORS.surface2}`;
                  e.currentTarget.style.color = COLORS.text;
                }
              }}
              onMouseLeave={e => {
                if (!isActive) {
                  e.currentTarget.style.background = 'transparent';
                  e.currentTarget.style.color = COLORS.textSecondary;
                }
              }}
            >
              <span style={{ fontSize: '16px', flexShrink: 0 }}>{item.icon}</span>
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Footer */}
      <div style={{ padding: '12px 16px', borderTop: `1px solid ${COLORS.border}` }}>
        <div style={{ color: COLORS.textSecondary, fontSize: '11px' }}>
          AgentForge Dashboard
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [isConfigured, setIsConfigured] = useState(null); // null = loading
  const [currentView, setCurrentView] = useState('dashboard');
  const [selectedTask, setSelectedTask] = useState(null);

  useEffect(() => {
    const check = async () => {
      try {
        const res = await fetch('/api/system/config');
        if (res.ok) {
          const data = await res.json();
          setIsConfigured(data.is_configured === true || data.configured === true);
        } else {
          // If endpoint not found, show wizard
          setIsConfigured(false);
        }
      } catch {
        setIsConfigured(false);
      }
    };
    check();
  }, []);

  const handleSetupComplete = () => {
    setIsConfigured(true);
    setCurrentView('dashboard');
  };

  const handleSelectTask = (task) => {
    setSelectedTask(task);
    setCurrentView('task-detail');
  };

  const handleBackFromTask = () => {
    setSelectedTask(null);
    setCurrentView('tasks');
  };

  const handleNavigate = (view) => {
    setCurrentView(view);
    if (view !== 'task-detail') {
      setSelectedTask(null);
    }
  };

  // Loading state
  if (isConfigured === null) {
    return (
      <div style={{
        minHeight: '100vh',
        background: COLORS.bg,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        color: COLORS.textSecondary,
        fontSize: '16px',
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '16px' }}>
          <span style={{ fontSize: '32px' }}>⚡</span>
          <span>Loading AgentForge...</span>
        </div>
      </div>
    );
  }

  // Setup wizard (full-screen)
  if (!isConfigured) {
    return <SetupWizard onComplete={handleSetupComplete} />;
  }

  const renderContent = () => {
    switch (currentView) {
      case 'dashboard':
        return <SystemStatus />;
      case 'tasks':
        return <TaskList onSelectTask={handleSelectTask} />;
      case 'task-detail':
        return (
          <TaskDetail
            taskId={selectedTask?.id || selectedTask?.task_id}
            onBack={handleBackFromTask}
          />
        );
      case 'prs':
        return <PRViewer />;
      case 'chat':
        return (
          <div style={{ height: 'calc(100vh - 48px)', display: 'flex', flexDirection: 'column' }}>
            <Chat />
          </div>
        );
      case 'settings':
        return <Settings />;
      default:
        return <SystemStatus />;
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      background: COLORS.bg,
      fontFamily: 'system-ui, -apple-system, sans-serif',
    }}>
      <Sidebar currentView={currentView} onNavigate={handleNavigate} />

      {/* Main content */}
      <div style={{
        marginLeft: '220px',
        padding: '28px 32px',
        minHeight: '100vh',
        boxSizing: 'border-box',
        ...(currentView === 'chat' ? { height: '100vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', padding: '28px 32px' } : {}),
      }}>
        {renderContent()}
      </div>
    </div>
  );
}
