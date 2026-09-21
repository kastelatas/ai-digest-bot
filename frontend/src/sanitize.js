// Превью поста в формате Telegram-HTML. Разрешаем только теги, которые понимает Telegram,
// и только http(s)-ссылки; всё остальное разворачивается в текст. Скрипты не исполняются:
// разбор идёт через DOMParser, а на страницу вставляется уже пересобранный безопасный HTML.
const ALLOWED = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'INS', 'S', 'STRIKE', 'DEL', 'CODE', 'PRE', 'A', 'BR']);

export function sanitizeTelegramHtml(html) {
  const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
  const out = document.createElement('div');
  const walk = (src, dst) => {
    src.childNodes.forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        dst.appendChild(document.createTextNode(node.nodeValue));
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (!ALLOWED.has(node.tagName)) { walk(node, dst); return; }
        const el = document.createElement(node.tagName.toLowerCase());
        if (node.tagName === 'A') {
          const href = node.getAttribute('href') || '';
          if (/^https?:\/\//i.test(href)) {
            el.setAttribute('href', href);
            el.setAttribute('target', '_blank');
            el.setAttribute('rel', 'noopener noreferrer');
          }
        }
        walk(node, el);
        dst.appendChild(el);
      }
    });
  };
  walk(doc.body.firstChild, out);
  return out.innerHTML;
}
