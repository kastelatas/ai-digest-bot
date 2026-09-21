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

export const fmtMoney = (value, currency) => (value === null || value === undefined ? '—' : `${nf.format(value)} ${currency}`);

export const LINK_STATUS_LABELS = { active: 'Активна', revoked: 'Отозвана' };

// Текст рекламного поста для копирования: {link} заменяется на ссылку, без плейсхолдера ссылка идёт последней строкой.
export function renderAdText(text, url) {
  const body = (text || '').trim();
  if (!body) return url;
  return body.includes('{link}') ? body.split('{link}').join(url) : `${body}\n\n${url}`;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // http-страница или запрет доступа к буферу — запасной путь через выделение
    const el = document.createElement('textarea');
    el.value = text;
    el.setAttribute('readonly', '');
    el.style.position = 'fixed';
    el.style.opacity = '0';
    document.body.appendChild(el);
    el.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* нечем копировать */ }
    document.body.removeChild(el);
    return ok;
  }
}
