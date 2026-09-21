import { useLayoutEffect, useRef, useState } from 'react';
import { fmtDayMonth, fmtInt } from './format.js';

// Спецификации марок: линия 2px, маркер r>=4 с 2px кольцом цвета поверхности, столбцы <=24px
// со скруглением 4px только на «свободном» конце, гридлайны — тонкие сплошные.
const MARGIN = { top: 12, right: 56, bottom: 26, left: 46 };
const BAR_MAX = 24;
const BAR_RADIUS = 4;

function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(640);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const apply = () => setWidth(Math.max(280, Math.floor(el.clientWidth)));
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

// «Красивые» целые деления оси: 0 / 5 / 10 …; счётчики — только целые
export function niceTicks(min, max, count = 4) {
  if (min === max) { min -= 1; max += 1; }
  const step0 = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const norm = step0 / mag;
  const step = Math.max(1, (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag);
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(v);
  return ticks;
}

function Tooltip({ x, width, title, rows }) {
  const flip = x > width * 0.6;
  return (
    <div className="tooltip" role="tooltip" style={{ left: x, transform: flip ? 'translateX(calc(-100% - 12px))' : 'translateX(12px)' }}>
      <div className="tooltip-title">{title}</div>
      {rows.map((r) => (
        <div className="tooltip-row" key={r.name}>
          <span className="key-line" style={{ background: r.color }} aria-hidden="true" />
          <strong>{r.value}</strong>
          <span className="tooltip-name">{r.name}</span>
        </div>
      ))}
    </div>
  );
}

export function Legend({ items, shape = 'rect' }) {
  if (items.length < 2) return null; // одна серия — название уже в заголовке
  return (
    <ul className="legend">
      {items.map((s) => (
        <li key={s.key}>
          <span className={shape === 'line' ? 'key-line' : 'key-rect'} style={{ background: s.color }} aria-hidden="true" />
          {s.name}
        </li>
      ))}
    </ul>
  );
}

/** Линия с лёгкой заливкой. points: [{t: Date, v: number, label?: string}] */
export function LineChart({ points, seriesName, color, height = 240, formatX }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const w = width - MARGIN.left - MARGIN.right;
  const h = height - MARGIN.top - MARGIN.bottom;

  if (points.length === 0) return <div ref={ref} className="chart-empty">Данных пока нет</div>;

  const t0 = points[0].t.getTime();
  const t1 = points[points.length - 1].t.getTime();
  const vals = points.map((p) => p.v);
  const pad = Math.max(1, Math.round((Math.max(...vals) - Math.min(...vals)) * 0.1));
  const yTicks = niceTicks(Math.max(0, Math.min(...vals) - pad), Math.max(...vals) + pad);
  const yMin = yTicks[0];
  const yMax = yTicks[yTicks.length - 1];
  const sx = (t) => (t1 === t0 ? w / 2 : ((t - t0) / (t1 - t0)) * w);
  const sy = (v) => h - ((v - yMin) / (yMax - yMin)) * h;

  const path = points.map((p, i) => `${i ? 'L' : 'M'}${sx(p.t.getTime()).toFixed(1)},${sy(p.v).toFixed(1)}`).join('');
  const area = `${path}L${sx(t1).toFixed(1)},${h}L${sx(t0).toFixed(1)},${h}Z`;

  const fmtX = formatX || ((d) => (t1 - t0 < 2 * 864e5
    ? `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
    : fmtDayMonth(d)));
  const xTickCount = Math.max(2, Math.min(6, Math.floor(w / 90)));
  const xTicks = t1 === t0 ? [t0] : Array.from({ length: xTickCount }, (_, i) => t0 + ((t1 - t0) * i) / (xTickCount - 1));

  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    let best = 0;
    points.forEach((p, i) => { if (Math.abs(sx(p.t.getTime()) - px) < Math.abs(sx(points[best].t.getTime()) - px)) best = i; });
    setHover(best);
  };

  const last = points[points.length - 1];
  const hp = hover === null ? null : points[hover];

  return (
    <div className="chart" ref={ref} style={{ height }}>
      <svg width={width} height={height} role="img" aria-label={`${seriesName}: график`}>
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {yTicks.map((v) => (
            <g key={v}>
              <line className="grid" x1={0} x2={w} y1={sy(v)} y2={sy(v)} />
              <text className="tick" x={-8} y={sy(v)} textAnchor="end" dominantBaseline="middle">{fmtInt(v)}</text>
            </g>
          ))}
          <path d={area} fill={color} fillOpacity="0.1" />
          <path d={path} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          {xTicks.map((t) => (
            <text key={t} className="tick" x={sx(t)} y={h + 18} textAnchor="middle">{fmtX(new Date(t))}</text>
          ))}
          {hp ? (
            <line className="crosshair" x1={sx(hp.t.getTime())} x2={sx(hp.t.getTime())} y1={0} y2={h} />
          ) : null}
          <circle cx={sx(last.t.getTime())} cy={sy(last.v)} r={4} fill={color} className="dot-ring" strokeWidth="2" />
          <text className="end-label" x={sx(last.t.getTime()) + 10} y={sy(last.v)} dominantBaseline="middle">{fmtInt(last.v)}</text>
          {hp ? <circle cx={sx(hp.t.getTime())} cy={sy(hp.v)} r={5} fill={color} className="dot-ring" strokeWidth="2" /> : null}
          <rect x={0} y={0} width={w} height={h} fill="transparent" onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
        </g>
      </svg>
      {hp ? (
        <Tooltip
          x={MARGIN.left + sx(hp.t.getTime())}
          width={width}
          title={hp.label || fmtX(hp.t)}
          rows={[{ name: seriesName, value: fmtInt(hp.v), color }]}
        />
      ) : null}
    </div>
  );
}

function barPath(x, base, tip, w) {
  const h = Math.abs(tip - base);
  if (h < 0.5) return '';
  const r = Math.min(BAR_RADIUS, h, w / 2);
  const up = tip < base;
  if (up) return `M${x},${base}V${tip + r}Q${x},${tip} ${x + r},${tip}H${x + w - r}Q${x + w},${tip} ${x + w},${tip + r}V${base}Z`;
  return `M${x},${base}V${tip - r}Q${x},${tip} ${x + r},${tip}H${x + w - r}Q${x + w},${tip} ${x + w},${tip - r}V${base}Z`;
}

/**
 * Столбцы от нулевой линии. data: [{label, day: Date, values: {key: number}}],
 * series: [{key, name, color}] — положительные значения растут вверх, отрицательные вниз.
 */
export function BarChart({ data, series, height = 220, valueFormat = fmtInt }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const w = width - MARGIN.left - MARGIN.right;
  const h = height - MARGIN.top - MARGIN.bottom;

  if (data.length === 0) return <div ref={ref} className="chart-empty">Данных пока нет</div>;

  const all = data.flatMap((d) => series.map((s) => d.values[s.key] || 0));
  const yTicks = niceTicks(Math.min(0, ...all), Math.max(1, ...all));
  const yMin = yTicks[0];
  const yMax = yTicks[yTicks.length - 1];
  const sy = (v) => h - ((v - yMin) / (yMax - yMin)) * h;
  const band = w / data.length;
  const bw = Math.min(BAR_MAX, Math.max(3, band * 0.6));
  const every = Math.ceil(data.length / Math.max(2, Math.floor(w / 56)));
  const hd = hover === null ? null : data[hover];

  return (
    <div className="chart" ref={ref} style={{ height }}>
      <svg width={width} height={height} role="img" aria-label="Столбчатый график">
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {yTicks.map((v) => (
            <g key={v}>
              <line className={v === 0 ? 'baseline' : 'grid'} x1={0} x2={w} y1={sy(v)} y2={sy(v)} />
              <text className="tick" x={-8} y={sy(v)} textAnchor="end" dominantBaseline="middle">{fmtInt(v)}</text>
            </g>
          ))}
          {data.map((d, i) => {
            const cx = band * i + band / 2;
            return (
              <g key={d.label + i} opacity={hover === null || hover === i ? 1 : 0.55}>
                {series.map((s) => {
                  const v = d.values[s.key] || 0;
                  return v ? <path key={s.key} d={barPath(cx - bw / 2, sy(0), sy(v), bw)} fill={s.color} /> : null;
                })}
                {i % every === 0 ? <text className="tick" x={cx} y={h + 18} textAnchor="middle">{d.label}</text> : null}
              </g>
            );
          })}
          {data.map((d, i) => (
            <rect
              key={`hit${i}`}
              x={band * i} y={0} width={band} height={h} fill="transparent"
              onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}
            />
          ))}
        </g>
      </svg>
      {hd ? (
        <Tooltip
          x={MARGIN.left + band * hover + band / 2}
          width={width}
          title={hd.title || hd.label}
          rows={series.map((s) => ({ name: s.name, value: valueFormat(hd.values[s.key] || 0), color: s.color }))}
        />
      ) : null}
    </div>
  );
}

/** Карточка: заголовок, легенда, график и таблица данных (доступная альтернатива hover). */
export function ChartCard({ title, subtitle, legend, legendShape, loading, children, table, note }) {
  return (
    <section className={`card chart-card${loading ? ' is-loading' : ''}`}>
      <header className="card-head">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p className="muted">{subtitle}</p> : null}
        </div>
        {legend ? <Legend items={legend} shape={legendShape} /> : null}
      </header>
      {children}
      {note ? <p className="note">{note}</p> : null}
      {table ? (
        <details className="table-view">
          <summary>Таблица данных</summary>
          <div className="table-scroll">
            <table>
              <thead><tr>{table.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                {table.rows.map((r, i) => (
                  <tr key={i}>{r.map((cell, j) => <td key={j} className={j ? 'num' : undefined}>{cell}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </section>
  );
}
