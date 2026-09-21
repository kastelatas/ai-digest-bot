import { useEffect, useState } from 'react';
import { api } from './api.js';
import { fmtDateTime, fmtInt } from './format.js';

// ссылки берём из config.yaml — рисуем только http(s)
const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : null);

function ExtLink({ href, children }) {
  const url = safeUrl(href);
  return url ? <a href={url} target="_blank" rel="noopener noreferrer">{children}</a> : <>{children}</>;
}

export default function Sources() {
  const [state, setState] = useState({ loading: true, error: null, items: [] });

  useEffect(() => {
    let cancelled = false;
    api.sources()
      .then((d) => { if (!cancelled) setState({ loading: false, error: null, items: d.items }); })
      .catch((e) => { if (!cancelled) setState({ loading: false, error: e.message, items: [] }); });
    return () => { cancelled = true; };
  }, []);

  const { loading, error, items } = state;
  const active = items.filter((s) => s.enabled).length;

  return (
    <section className={`card${loading ? ' is-loading' : ''}`}>
      <header className="card-head">
        <div>
          <h2>Источники новостей</h2>
          <p className="muted">
            {items.length ? `Активных лент: ${active} из ${items.length}. ` : ''}
            Список задаётся в config.yaml (секция sources): сбор подхватывает правки сам, а эта таблица обновится после перезапуска панели.
          </p>
        </div>
      </header>
      {error ? <div className="alert" role="alert">Не удалось загрузить источники: {error}</div> : null}
      <div className="table-scroll">
        <table className="sources">
          <thead>
            <tr>
              <th>Источник</th>
              <th>Язык</th>
              <th className="num">Собрано</th>
              <th className="num">Черновиков</th>
              <th className="num">Опубликовано</th>
              <th>Последний сбор</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={6} className="muted empty">{loading ? 'Загрузка…' : 'Источников в config.yaml нет'}</td></tr>
            ) : items.map((s) => (
              <tr key={s.name} className={s.enabled ? undefined : 'is-revoked'}>
                <td className="post-cell">
                  <div className="post-title">
                    <ExtLink href={s.site_url}>{s.name}</ExtLink>
                    {s.enabled ? null : <span className="chip muted-chip">Выключен</span>}
                  </div>
                  <div className="post-snippet">
                    <ExtLink href={s.feed_url}>RSS-лента ↗</ExtLink>
                    <span className="muted"> · {s.category}</span>
                  </div>
                </td>
                <td>{s.lang.toUpperCase()}</td>
                <td className="num">{fmtInt(s.fetched)}</td>
                <td className="num">{fmtInt(s.drafts)}</td>
                <td className="num">{fmtInt(s.published)}</td>
                <td className="nowrap">{fmtDateTime(s.last_fetched_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">«Собрано» — новости из ленты, дошедшие до базы; «Черновиков» — из них написано постов; «Опубликовано» — вышло в канал.</p>
    </section>
  );
}
