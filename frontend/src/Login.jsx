import { useState } from 'react';
import { api } from './api.js';

export default function Login({ onDone }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      onDone();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <form className="card login-card" onSubmit={submit}>
        <h1>Панель дайджеста</h1>
        <label htmlFor="pw">Пароль администратора</label>
        <input id="pw" type="password" autoFocus autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        {error ? <div className="alert" role="alert">{error}</div> : null}
        <button className="btn primary" type="submit" disabled={busy || !password}>{busy ? 'Проверяю…' : 'Войти'}</button>
      </form>
    </main>
  );
}
