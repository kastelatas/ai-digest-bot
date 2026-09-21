import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api.js';
import { sanitizeTelegramHtml } from './sanitize.js';

const LIMIT = 4096;
const MODES = [
  { value: 'draft', title: 'Сохранить черновик', hint: 'останется в списке, в канал не уйдёт' },
  { value: 'queue', title: 'В очередь', hint: 'выйдет в ближайший слот публикации' },
  { value: 'publish', title: 'Опубликовать сейчас', hint: 'сразу уйдёт в канал' },
];

export default function PostEditor({ post, telegramEnabled, onClose, onSaved }) {
  const editing = Boolean(post);
  const published = editing && post.status === 'published';
  const [text, setText] = useState(post ? post.text : '');
  const [mode, setMode] = useState('draft');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [links, setLinks] = useState([]);
  const textRef = useRef(null);
  const preview = useMemo(() => sanitizeTelegramHtml(text), [text]);
  const tooLong = text.length > LIMIT;

  // активные ссылки-приглашения — их можно вставить в текст одним выбором
  useEffect(() => {
    api.links().then((d) => setLinks(d.items.filter((l) => l.status === 'active'))).catch(() => {});
  }, []);

  const insertLink = (id) => {
    const link = links.find((l) => String(l.id) === id);
    if (!link) return;
    const html = `<a href="${link.url}">Подписаться</a>`;
    const el = textRef.current;
    const at = el ? [el.selectionStart, el.selectionEnd] : [text.length, text.length];
    setText(text.slice(0, at[0]) + html + text.slice(at[1]));
    if (el) el.focus();
  };

  const save = async () => {
    if (!editing && mode === 'publish' && !window.confirm('Опубликовать пост в канал прямо сейчас?')) return;
    if (published && !window.confirm('Пост уже в канале — изменится само сообщение в Telegram. Продолжить?')) return;
    setBusy(true);
    setError(null);
    try {
      const saved = editing ? await api.updatePost(post.id, { text }) : await api.createPost(text, mode);
      onSaved(saved, editing ? 'Пост обновлён' : mode === 'publish' ? 'Пост опубликован' : 'Пост создан');
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const needsTelegram = published || (!editing && mode === 'publish');

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={editing ? 'Изменить пост' : 'Новый пост'}>
      <div className="modal card">
        <header className="card-head">
          <h2>{editing ? `Пост №${post.id}` : 'Новый пост'}</h2>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="Закрыть">✕</button>
        </header>

        {published ? <div className="alert warn">Пост уже опубликован. Сохранение изменит сообщение в канале.</div> : null}

        <div className="editor-grid">
          <div>
            <label htmlFor="post-text">Текст (Telegram-HTML: &lt;b&gt;, &lt;i&gt;, &lt;a href&gt;, &lt;code&gt;)</label>
            <textarea id="post-text" ref={textRef} rows={14} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
            {links.length ? (
              <select className="insert-link" value="" onChange={(e) => insertLink(e.target.value)} aria-label="Вставить ссылку-приглашение">
                <option value="">＋ Вставить ссылку-приглашение…</option>
                {links.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
            ) : null}
            <div className={`counter${tooLong ? ' over' : ''}`}>{text.length} / {LIMIT}</div>
          </div>
          <div>
            <div className="label">Предпросмотр</div>
            <div className="preview" dangerouslySetInnerHTML={{ __html: preview || '<span class="muted">Пусто</span>' }} />
          </div>
        </div>

        {!editing ? (
          <fieldset className="modes">
            <legend>Что сделать</legend>
            {MODES.map((m) => (
              <label key={m.value} className={`mode${mode === m.value ? ' is-active' : ''}`}>
                <input type="radio" name="mode" value={m.value} checked={mode === m.value} onChange={() => setMode(m.value)} />
                <span><strong>{m.title}</strong><span className="muted"> — {m.hint}</span></span>
              </label>
            ))}
          </fieldset>
        ) : null}

        {needsTelegram && !telegramEnabled ? (
          <div className="alert" role="alert">На сервере не задан TELEGRAM_BOT_TOKEN — действия в канале недоступны.</div>
        ) : null}
        {error ? <div className="alert" role="alert">{error}</div> : null}

        <footer className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>Отмена</button>
          <button
            type="button"
            className="btn primary"
            onClick={save}
            disabled={busy || !text.trim() || tooLong || (needsTelegram && !telegramEnabled)}
          >
            {busy ? 'Сохраняю…' : editing ? 'Сохранить' : MODES.find((m) => m.value === mode).title}
          </button>
        </footer>
      </div>
    </div>
  );
}
