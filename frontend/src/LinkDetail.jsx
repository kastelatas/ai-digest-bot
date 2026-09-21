import { useEffect, useState } from 'react';
import { api } from './api.js';
import { BarChart, ChartCard } from './Charts.jsx';
import { fmtDateTime, fmtDayMonth, fmtInt, fmtMoney, fmtSigned, parseDay } from './format.js';

const RANGES = [7, 30, 90];
const BLUE = 'var(--series-1)';
const ORANGE = 'var(--series-2)';

// linkId === null — вступления не по нашим ссылкам (поиск, @username, чужие ссылки)
export default function LinkDetail({ linkId, onClose }) {
  const [days, setDays] = useState(30);
  const [state, setState] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    api.linkDaily(linkId, days)
      .then((data) => { if (!cancelled) setState({ loading: false, error: null, data }); })
      .catch((e) => { if (!cancelled) setState((s) => ({ ...s, loading: false, error: e.message })); });
    return () => { cancelled = true; };
  }, [linkId, days]);

  const { loading, error, data } = state;
  const link = data ? data.link : null;
  const daily = data ? data.daily : [];
  const periodJoined = daily.reduce((n, r) => n + r.joined, 0);
  const periodLeft = daily.reduce((n, r) => n + r.left, 0);
  const bars = daily.map((r) => ({ label: fmtDayMonth(parseDay(r.date)), values: { joined: r.joined, left: -r.left } }));
  const series = [{ key: 'joined', name: 'Вступили', color: BLUE }, { key: 'left', name: 'Ушли', color: ORANGE }];

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Статистика ссылки">
      <div className="modal card">
        <header className="card-head">
          <div>
            <h2>{linkId === null ? 'Пришли не по нашим ссылкам' : link ? link.name : 'Ссылка'}</h2>
            {link ? <p className="muted">создана {fmtDateTime(link.created_at)}{link.notes ? ` · ${link.notes}` : ''}</p> : null}
            {linkId === null ? <p className="muted">Поиск, @username и чужие ссылки-приглашения</p> : null}
          </div>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="Закрыть">✕</button>
        </header>

        <div className="filters" role="group" aria-label="Период">
          {RANGES.map((r) => (
            <button key={r} type="button" className={`seg${days === r ? ' is-active' : ''}`} aria-pressed={days === r} onClick={() => setDays(r)}>{r} дн.</button>
          ))}
        </div>

        {error ? <div className="alert" role="alert">Не удалось загрузить данные: {error}</div> : null}

        <div className={loading ? 'is-loading' : undefined}>
          <div className="tiles">
            <div className="tile"><div className="tile-label">Вступили за {days} дн.</div><div className="tile-value">{fmtInt(periodJoined)}</div></div>
            <div className="tile"><div className="tile-label">Ушли за {days} дн.</div><div className="tile-value">{periodLeft ? `−${fmtInt(periodLeft)}` : '0'}</div></div>
            {link ? (
              <>
                <div className="tile"><div className="tile-label">Вступило за всё время</div><div className="tile-value">{fmtInt(link.joined)}</div>
                  <span className="muted">осталось {fmtInt(link.retained)}{link.retention_pct !== null ? ` (${link.retention_pct}%)` : ''}</span></div>
                <div className="tile"><div className="tile-label">Цена подписчика</div><div className="tile-value">{fmtMoney(link.cost_per_join, link.currency)}</div>
                  <span className="muted">с учётом ушедших: {fmtMoney(link.cost_per_retained, link.currency)}</span></div>
              </>
            ) : null}
          </div>

          <ChartCard
            title="Вступления и выходы по дням"
            subtitle="Вступили — вверх, ушли — вниз"
            loading={loading}
            legend={series}
            table={{
              columns: ['Дата', 'Вступили', 'Ушли', 'Итог'],
              rows: daily.map((r) => [fmtDayMonth(parseDay(r.date)), fmtInt(r.joined), fmtInt(r.left), fmtSigned(r.joined - r.left)]),
            }}
          >
            <BarChart data={bars} series={series} valueFormat={fmtSigned} />
          </ChartCard>
        </div>

        <footer className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>Закрыть</button>
        </footer>
      </div>
    </div>
  );
}
