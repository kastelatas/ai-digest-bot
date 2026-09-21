import { useCallback, useEffect, useState } from 'react';
import { api, setUnauthorizedHandler } from './api.js';
import Dashboard from './Dashboard.jsx';
import Links from './Links.jsx';
import Login from './Login.jsx';
import Posts from './Posts.jsx';

const THEMES = [['system', 'Авто'], ['light', 'Светлая'], ['dark', 'Тёмная']];

function useTheme() {
  const read = () => { try { return localStorage.getItem('theme') || 'system'; } catch { return 'system'; } };
  const [theme, setTheme] = useState(read);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', theme);
    try { localStorage.setItem('theme', theme); } catch { /* приватный режим */ }
  }, [theme]);
  return [theme, setTheme];
}

export default function App() {
  const [me, setMe] = useState(null); // null — ещё грузим
  const [tab, setTab] = useState('metrics');
  const [theme, setTheme] = useTheme();

  const refresh = useCallback(() => api.me().then(setMe).catch(() => setMe({ authenticated: false })), []);
  useEffect(() => {
    setUnauthorizedHandler(() => setMe((m) => ({ ...(m || {}), authenticated: false })));
    refresh();
  }, [refresh]);

  if (me === null) return <div className="boot" aria-busy="true">Загрузка…</div>;
  if (!me.authenticated) return <Login onDone={refresh} />;

  const logout = async () => { await api.logout().catch(() => {}); refresh(); };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <strong>Панель дайджеста</strong>
          <span className="muted">{me.channel}</span>
        </div>
        <nav className="tabs" aria-label="Разделы">
          <button type="button" className={`tab${tab === 'metrics' ? ' is-active' : ''}`} aria-current={tab === 'metrics' ? 'page' : undefined} onClick={() => setTab('metrics')}>Метрики</button>
          <button type="button" className={`tab${tab === 'posts' ? ' is-active' : ''}`} aria-current={tab === 'posts' ? 'page' : undefined} onClick={() => setTab('posts')}>Посты</button>
          <button type="button" className={`tab${tab === 'links' ? ' is-active' : ''}`} aria-current={tab === 'links' ? 'page' : undefined} onClick={() => setTab('links')}>Ссылки</button>
        </nav>
        <div className="topbar-right">
          <select value={theme} onChange={(e) => setTheme(e.target.value)} aria-label="Тема оформления">
            {THEMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <button type="button" className="btn" onClick={logout}>Выйти</button>
        </div>
      </header>
      <main className="content">
        {tab === 'metrics' ? <Dashboard />
          : tab === 'posts' ? <Posts telegramEnabled={me.telegram_enabled} />
            : <Links telegramEnabled={me.telegram_enabled} defaultCurrency={me.currency} />}
      </main>
    </div>
  );
}
