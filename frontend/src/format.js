const nf = new Intl.NumberFormat('ru-RU');
export const fmtInt = (n) => (n === null || n === undefined ? '—' : nf.format(n));
export const fmtSigned = (n) => (n === null || n === undefined ? '—' : `${n > 0 ? '+' : n < 0 ? '−' : ''}${nf.format(Math.abs(n))}`);

const pad = (n) => String(n).padStart(2, '0');
export const fmtDayMonth = (d) => `${pad(d.getDate())}.${pad(d.getMonth() + 1)}`;
export const fmtDateTime = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${fmtDayMonth(d)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
// 'YYYY-MM-DD' -> Date в локальном поясе браузера (без сдвига из-за UTC)
export const parseDay = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };

export const STATUS_LABELS = {
  pending: 'Ждёт решения',
  approved: 'В очереди',
  needs_edit: 'Нужны правки',
  rejected: 'Отклонён',
  published: 'Опубликован',
  failed: 'Ошибка',
};
export const STATUS_ICONS = { pending: '◷', approved: '➜', needs_edit: '✎', rejected: '✕', published: '✓', failed: '!' };
