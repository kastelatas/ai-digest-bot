import { useCallback, useEffect, useState } from 'react';
import { api } from './api.js';
import { STATUS_ICONS, STATUS_LABELS, fmtDateTime, fmtInt } from './format.js';
import PostEditor from './PostEditor.jsx';

const PAGE = 25;

// какие смены статуса предлагаем для каждого статуса (published не меняется)
const TRANSITIONS = {
  pending: [['approved', 'Одобрить'], ['rejected', 'Отклонить']],
  approved: [['pending', 'Снять с очереди']],
  needs_edit: [['approved', 'В очередь'], ['pending', 'В черновики']],
  rejected: [['pending', 'В черновики']],
  failed: [['pending', 'В черновики']],
  published: [],
};

// статусы, для которых есть кнопка «Опубликовать сейчас» (published и rejected — нет)
const PUBLISHABLE = new Set(['pending', 'approved', 'needs_edit', 'failed']);

function StatusChip({ status }) {
  return <span className={`chip status-${status}`}><span aria-hidden="true">{STATUS_ICONS[status]}</span> {STATUS_LABELS[status] || status}</span>;
}

export default function Posts({ telegramEnabled }) {
  const [filters, setFilters] = useState({ status: '', q: '' });
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState({ total: 0, items: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);
  const [editor, setEditor] = useState(null); // null | {post?: Post}

  // поиск с задержкой, чтобы не дёргать API на каждую букву
  useEffect(() => {
    const t = setTimeout(() => { setFilters((f) => ({ ...f, q: query })); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [query]);

  const load = useCallback(() => {
    setLoading(true);
    api.posts({ status: filters.status, q: filters.q, limit: PAGE, offset })
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filters, offset]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const run = async (fn, okMessage) => {
    try {
      await fn();
      setToast({ ok: true, text: okMessage });
      load();
    } catch (e) {
      setToast({ ok: false, text: e.message });
    }
  };

  const publishNow = (post) => {
    if (!telegramEnabled) { setToast({ ok: false, text: 'На сервере не задан TELEGRAM_BOT_TOKEN — публикация недоступна' }); return; }
    if (!window.confirm(`Опубликовать пост №${post.id} в канал прямо сейчас, не дожидаясь слота?`)) return;
    run(() => api.publishPost(post.id), `Пост №${post.id} опубликован в канале`);
  };

  const remove = (post) => {
    const inChannel = post.status === 'published';
    const question = inChannel
      ? `Удалить пост №${post.id} из канала и из панели? Сообщение в Telegram исчезнет у всех подписчиков.`
      : `Удалить пост №${post.id}?`;
    if (!window.confirm(question)) return;
    run(async () => {
      try {
        await api.deletePost(post.id);
      } catch (e) {
        // Telegram отказал удалить сообщение — предлагаем убрать хотя бы запись из панели
        if (inChannel && e.status === 502 && window.confirm(`${e.message}\n\nУдалить только запись из панели?`)) {
          await api.deletePost(post.id, true);
        } else {
          throw e;
        }
      }
    }, 'Пост удалён');
  };

  const pages = Math.max(1, Math.ceil(data.total / PAGE));
  const page = Math.floor(offset / PAGE) + 1;

  return (
    <div>
      <div className="filters">
        <select value={filters.status} onChange={(e) => { setFilters({ ...filters, status: e.target.value }); setOffset(0); }} aria-label="Статус">
          <option value="">Все статусы</option>
          {Object.entries(STATUS_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <input type="search" placeholder="Поиск по тексту" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Поиск" />
        <span className="muted filters-note">Всего: {fmtInt(data.total)}</span>
        <button type="button" className="btn primary push-right" onClick={() => setEditor({})}>＋ Новый пост</button>
      </div>

      {toast ? <div className={`alert ${toast.ok ? 'ok' : ''}`} role="status">{toast.text}</div> : null}
      {error ? <div className="alert" role="alert">Не удалось загрузить посты: {error}</div> : null}

      <section className={`card${loading ? ' is-loading' : ''}`}>
        <div className="table-scroll">
          <table className="posts">
            <thead>
              <tr><th>№</th><th>Статус</th><th>Пост</th><th>Дата</th><th className="num">Просмотры</th><th>Действия</th></tr>
            </thead>
            <tbody>
              {data.items.length === 0 ? (
                <tr><td colSpan={6} className="muted empty">{loading ? 'Загрузка…' : 'Постов нет'}</td></tr>
              ) : data.items.map((p) => (
                <tr key={p.id}>
                  <td className="num">{p.id}</td>
                  <td><StatusChip status={p.status} /></td>
                  <td className="post-cell">
                    <div className="post-title">{p.title || 'Без заголовка'}</div>
                    <div className="muted post-snippet">{p.source}</div>
                  </td>
                  <td className="nowrap">{fmtDateTime(p.published_at || p.created_at)}</td>
                  <td className="num">{fmtInt(p.views)}</td>
                  <td className="actions">
                    {PUBLISHABLE.has(p.status) ? (
                      <button type="button" className="btn small primary" onClick={() => publishNow(p)} disabled={!telegramEnabled}>Опубликовать сейчас</button>
                    ) : null}
                    {(TRANSITIONS[p.status] || []).map(([to, label]) => (
                      <button key={to} type="button" className="btn small" onClick={() => run(() => api.updatePost(p.id, { status: to }), `Статус: ${label}`)}>{label}</button>
                    ))}
                    {p.channel_url ? <a className="btn small" href={p.channel_url} target="_blank" rel="noopener noreferrer">В канале ↗</a> : null}
                    <button type="button" className="btn small" onClick={() => setEditor({ post: p })}>Изменить</button>
                    <button type="button" className="btn small danger" onClick={() => remove(p)}>Удалить</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="pager">
          <button type="button" className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Назад</button>
          <span className="muted">Страница {page} из {pages}</span>
          <button type="button" className="btn small" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Вперёд →</button>
        </div>
      </section>

      {editor ? (
        <PostEditor
          post={editor.post}
          telegramEnabled={telegramEnabled}
          onClose={() => setEditor(null)}
          onSaved={(_, message) => { setEditor(null); setToast({ ok: true, text: message }); load(); }}
        />
      ) : null}
    </div>
  );
}
