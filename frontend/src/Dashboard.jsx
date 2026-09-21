import { useEffect, useState } from 'react';
import { api } from './api.js';
import { BarChart, ChartCard, LineChart } from './Charts.jsx';
import Sources from './Sources.jsx';
import { fmtDateTime, fmtInt, fmtSigned, parseDay, fmtDayMonth } from './format.js';

const RANGES = [7, 30, 90];
const BLUE = 'var(--series-1)';
const ORANGE = 'var(--series-2)';

function Delta({ value, label }) {
  if (value === null || value === undefined) return <span className="delta muted">{label}: нет данных</span>;
  const arrow = value > 0 ? '▲' : value < 0 ? '▼' : '■';
  return <span className="delta">{arrow} {fmtSigned(value)} <span className="muted">{label}</span></span>;
}

function Tile({ label, value, children }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {children}
    </div>
  );
}

export default function Dashboard() {
  const [days, setDays] = useState(30);
  const [state, setState] = useState({ loading: true, error: null, overview: null, subs: null, posts: null });

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    Promise.all([api.overview(days), api.subscribers(days), api.postsDaily(days)])
      .then(([overview, subs, posts]) => { if (!cancelled) setState({ loading: false, error: null, overview, subs, posts }); })
      .catch((e) => { if (!cancelled) setState((s) => ({ ...s, loading: false, error: e.message })); });
    return () => { cancelled = true; };
  }, [days]);

  const { loading, error, overview: ov, subs, posts } = state;

  // Линия: по сырым замерам, если их ≥2 (точнее), иначе по дням
  const rawPoints = subs ? subs.points.map((p) => ({ t: new Date(p.t), v: p.subscribers })) : [];
  const dailyPoints = subs ? subs.daily.filter((r) => r.subscribers !== null).map((r) => ({ t: parseDay(r.date), v: r.subscribers })) : [];
  const linePoints = rawPoints.length >= 2 ? rawPoints : dailyPoints;

  const subsBars = subs
    ? subs.daily.map((r) => ({ label: fmtDayMonth(parseDay(r.date)), title: fmtDayMonth(parseDay(r.date)), values: { gained: r.gained, lost: -r.lost } }))
    : [];
  const postBars = posts
    ? posts.daily.map((r) => ({ label: fmtDayMonth(parseDay(r.date)), values: { published: r.published } }))
    : [];

  return (
    <div>
      <div className="filters" role="group" aria-label="Период">
        {RANGES.map((r) => (
          <button key={r} type="button" className={`seg${days === r ? ' is-active' : ''}`} aria-pressed={days === r} onClick={() => setDays(r)}>
            {r} дн.
          </button>
        ))}
        {ov ? <span className="muted filters-note">Последний замер: {fmtDateTime(ov.last_snapshot_at)} · часовой пояс {ov.timezone}</span> : null}
      </div>

      {error ? <div className="alert" role="alert">Не удалось загрузить данные: {error}</div> : null}

      <div className={loading ? 'is-loading' : undefined}>
        <div className="tiles">
          <div className="tile hero">
            <div className="tile-label">Подписчиков сейчас</div>
            <div className="hero-value">{fmtInt(ov ? ov.subscribers : null)}</div>
            <div className="deltas">
              <Delta value={ov ? ov.delta_1d : null} label="за сутки" />
              <Delta value={ov ? ov.delta_7d : null} label="за 7 дней" />
              <Delta value={ov ? ov.delta_30d : null} label="за 30 дней" />
            </div>
          </div>
          <Tile label={`Подписки за ${days} дн.`} value={fmtSigned(ov ? ov.gained : null)} />
          <Tile label={`Отписки за ${days} дн.`} value={ov ? (ov.lost ? `−${fmtInt(ov.lost)}` : '0') : '—'} />
          <Tile label={`Опубликовано за ${days} дн.`} value={fmtInt(ov ? ov.posts_published_period : null)} />
          <Tile label="Ждут решения" value={fmtInt(ov ? ov.posts_by_status.pending || 0 : null)}>
            <span className="muted">в очереди: {fmtInt(ov ? ov.posts_by_status.approved || 0 : null)}</span>
          </Tile>
        </div>

        <ChartCard
          title="Подписчики"
          subtitle="Число подписчиков канала по замерам"
          loading={loading}
          table={{
            columns: ['Дата', 'Подписчиков', 'Замеров'],
            rows: (subs ? subs.daily : []).map((r) => [fmtDayMonth(parseDay(r.date)), fmtInt(r.subscribers), fmtInt(r.snapshots)]),
          }}
        >
          <LineChart points={linePoints} seriesName="Подписчики" color={BLUE} />
        </ChartCard>

        <ChartCard
          title="Подписки и отписки по дням"
          subtitle="Прирост вверх, убыль вниз"
          loading={loading}
          legend={[{ key: 'gained', name: 'Подписки', color: BLUE }, { key: 'lost', name: 'Отписки', color: ORANGE }]}
          note="Telegram Bot API отдаёт только текущее число подписчиков, поэтому подписки и отписки считаются по разнице между соседними замерами. Если внутри одного интервала кто-то и подписался, и отписался, это взаимно гасится. Чем чаще замеры, тем точнее."
          table={{
            columns: ['Дата', 'Подписки', 'Отписки', 'Итог'],
            rows: (subs ? subs.daily : []).map((r) => [fmtDayMonth(parseDay(r.date)), fmtInt(r.gained), fmtInt(r.lost), fmtSigned(r.net)]),
          }}
        >
          <BarChart
            data={subsBars}
            series={[{ key: 'gained', name: 'Подписки', color: BLUE }, { key: 'lost', name: 'Отписки', color: ORANGE }]}
            valueFormat={fmtSigned}
          />
        </ChartCard>

        <ChartCard
          title="Публикации по дням"
          subtitle="Сколько постов вышло в канал"
          loading={loading}
          table={{
            columns: ['Дата', 'Опубликовано'],
            rows: (posts ? posts.daily : []).map((r) => [fmtDayMonth(parseDay(r.date)), fmtInt(r.published)]),
          }}
        >
          <BarChart data={postBars} series={[{ key: 'published', name: 'Опубликовано', color: BLUE }]} />
        </ChartCard>
      </div>

      <Sources />
    </div>
  );
}
