import { useCallback, useEffect, useState } from 'react';
import { api } from './api.js';
import { LINK_STATUS_LABELS, copyText, fmtDateTime, fmtInt, fmtMoney, renderAdText } from './format.js';
import LinkDetail from './LinkDetail.jsx';
import LinkEditor from './LinkEditor.jsx';

export default function Links({ telegramEnabled, defaultCurrency }) {
  const [data, setData] = useState({ items: [], organic: { joined: 0, left: 0, retained: 0 }, tracking_since: null });
  const [showRevoked, setShowRevoked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);
  const [editor, setEditor] = useState(null); // null | {link?}
  const [detail, setDetail] = useState(undefined); // undefined — закрыто, null — «не по нашим ссылкам», число — id

  const load = useCallback(() => {
    setLoading(true);
    api.links()
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  const copy = async (text, okMessage) => {
    const ok = await copyText(text);
    setToast({ ok, text: ok ? okMessage : 'Не удалось скопировать — выделите и скопируйте вручную' });
  };

  const revoke = async (link) => {
    if (!window.confirm(`Отозвать ссылку «${link.name}»? Новые вступления по ней прекратятся, накопленная статистика останется.`)) return;
    try {
      await api.revokeLink(link.id);
      setToast({ ok: true, text: 'Ссылка отозвана' });
    } catch (e) {
      // Telegram отказал (например, ссылку уже отозвали руками) — предлагаем пометить её отозванной хотя бы в панели
      if (e.status === 502 && window.confirm(`${e.message}\n\nПометить отозванной только в панели?`)) {
        try {
          await api.revokeLink(link.id, true);
          setToast({ ok: true, text: 'Ссылка помечена отозванной в панели' });
        } catch (e2) {
          setToast({ ok: false, text: e2.message });
        }
      } else {
        setToast({ ok: false, text: e.message });
      }
    }
    load();
  };

  const visible = data.items.filter((l) => showRevoked || l.status === 'active');
  const hiddenCount = data.items.length - visible.length;
  const totalJoined = data.items.reduce((n, l) => n + l.joined, 0);
  const noEventsYet = data.items.length > 0 && totalJoined === 0 && data.organic.joined === 0;
  const organic = data.organic;

  return (
    <div>
      <div className="filters">
        <label className="check">
          <input type="checkbox" checked={showRevoked} onChange={(e) => setShowRevoked(e.target.checked)} />
          Показывать отозванные{hiddenCount && !showRevoked ? ` (${hiddenCount})` : ''}
        </label>
        <span className="muted filters-note">
          {data.tracking_since ? `Вступления считаются с ${fmtDateTime(data.tracking_since)}` : 'Учёт вступлений ещё не начался — запустите moderate'}
        </span>
        <button type="button" className="btn primary push-right" onClick={() => setEditor({})}>＋ Новая ссылка</button>
      </div>

      {toast ? <div className={`alert ${toast.ok ? 'ok' : ''}`} role="status">{toast.text}</div> : null}
      {error ? <div className="alert" role="alert">Не удалось загрузить ссылки: {error}</div> : null}
      {noEventsYet ? (
        <div className="alert warn" role="status">
          Пока ни одного вступления не зафиксировано. Это нормально, если ссылки ещё не опубликованы. Если люди уже вступают, проверьте, что
          бот — админ канала, а команда <code>moderate</code> запускается по расписанию (каждую минуту): именно она получает события вступления.
        </div>
      ) : null}

      <section className={`card${loading ? ' is-loading' : ''}`}>
        <div className="table-scroll">
          <table className="links">
            <thead>
              <tr>
                <th>Ссылка</th>
                <th className="num">Вступило</th>
                <th className="num">За 24 ч</th>
                <th className="num">Ушло</th>
                <th className="num">Осталось</th>
                <th className="num">Цена подписчика</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 ? (
                <tr><td colSpan={7} className="muted empty">{loading ? 'Загрузка…' : 'Ссылок пока нет — создайте первую под закуп рекламы'}</td></tr>
              ) : visible.map((l) => (
                <tr key={l.id} className={l.status === 'revoked' ? 'is-revoked' : undefined}>
                  <td className="post-cell">
                    <div className="post-title">{l.name}{l.status === 'revoked' ? <span className="chip muted-chip">{LINK_STATUS_LABELS.revoked}</span> : null}</div>
                    <div className="muted post-snippet">
                      создана {fmtDateTime(l.created_at)}{l.notes ? ` · ${l.notes}` : ''}
                    </div>
                  </td>
                  <td className="num">{fmtInt(l.joined)}</td>
                  <td className="num">{fmtInt(l.joined_24h)}</td>
                  <td className="num">{fmtInt(l.left)}</td>
                  <td className="num">{fmtInt(l.retained)}{l.retention_pct !== null ? <span className="muted"> · {l.retention_pct}%</span> : null}</td>
                  <td className="num" title={l.cost !== null ? `Всего заплачено: ${fmtMoney(l.cost, l.currency)}` : undefined}>
                    {fmtMoney(l.cost_per_join, l.currency)}
                  </td>
                  <td className="actions">
                    <button type="button" className="btn small primary" onClick={() => copy(l.url, 'Ссылка скопирована')}>Копировать ссылку</button>
                    {l.ad_text.trim() ? (
                      <button type="button" className="btn small" onClick={() => copy(renderAdText(l.ad_text, l.url), 'Текст рекламы скопирован')}>Копировать текст</button>
                    ) : null}
                    <button type="button" className="btn small" onClick={() => setDetail(l.id)}>Статистика</button>
                    <button type="button" className="btn small" onClick={() => setEditor({ link: l })}>Изменить</button>
                    {l.status === 'active' ? <button type="button" className="btn small danger" onClick={() => revoke(l)}>Отозвать</button> : null}
                  </td>
                </tr>
              ))}
              <tr className="organic-row">
                <td className="post-cell">
                  <div className="post-title muted">Не по нашим ссылкам</div>
                  <div className="muted post-snippet">поиск, @username, чужие ссылки-приглашения</div>
                </td>
                <td className="num">{fmtInt(organic.joined)}</td>
                <td className="num">—</td>
                <td className="num">{fmtInt(organic.left)}</td>
                <td className="num">{fmtInt(organic.retained)}</td>
                <td className="num">—</td>
                <td className="actions"><button type="button" className="btn small" onClick={() => setDetail(null)}>Статистика</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="note">
          Вступление засчитывается той ссылке, по которой человек пришёл; уход — той, по которой он вступал. «Цена подписчика» — стоимость закупа,
          делённая на число вступивших (полная стоимость видна во всплывающей подсказке). Люди, вступившие до начала учёта, здесь не видны.
        </p>
      </section>

      {editor ? (
        <LinkEditor
          link={editor.link}
          defaultCurrency={defaultCurrency}
          telegramEnabled={telegramEnabled}
          onClose={() => setEditor(null)}
          onCopy={copy}
          onSaved={(_, message) => { setEditor(null); setToast({ ok: true, text: message }); load(); }}
        />
      ) : null}
      {detail !== undefined ? <LinkDetail linkId={detail} onClose={() => setDetail(undefined)} /> : null}
    </div>
  );
}
