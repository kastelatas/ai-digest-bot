import { useState } from 'react';
import { api } from './api.js';
import { renderAdText } from './format.js';

const LIMIT = 4096;
const NAME_MAX = 64;
const PLACEHOLDER_URL = 'https://t.me/+ваша-ссылка';

export default function LinkEditor({ link: initialLink, defaultCurrency, telegramEnabled, onClose, onSaved, onChanged, onCopy }) {
  // после «Сгенерировать ссылку» окно остаётся открытым и переходит в режим правки уже созданной ссылки
  const [link, setLink] = useState(initialLink || null);
  const [justCreated, setJustCreated] = useState(false);
  const editing = Boolean(link);
  const [name, setName] = useState(initialLink ? initialLink.name : '');
  const [cost, setCost] = useState(initialLink && initialLink.cost !== null ? String(initialLink.cost) : '');
  const [currency, setCurrency] = useState(initialLink ? initialLink.currency : defaultCurrency);
  const [notes, setNotes] = useState(initialLink ? initialLink.notes : '');
  const [adText, setAdText] = useState(initialLink ? initialLink.ad_text : '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const costValue = cost.trim() === '' ? null : Number(cost.replace(',', '.'));
  const costInvalid = costValue !== null && (!Number.isFinite(costValue) || costValue < 0);
  const url = link ? link.url : PLACEHOLDER_URL;
  const canSave = name.trim() && !costInvalid && adText.length <= LIMIT && (editing || telegramEnabled);

  const save = async () => {
    setBusy(true);
    setError(null);
    const body = { name, cost: costValue, currency, notes, ad_text: adText };
    try {
      if (editing) {
        const saved = await api.updateLink(link.id, body);
        onSaved(saved, 'Ссылка обновлена');
      } else {
        const created = await api.createLink(body);
        setLink(created);
        setJustCreated(true);
        onChanged();
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={editing ? 'Изменить ссылку' : 'Новая ссылка'}>
      <div className="modal card">
        <header className="card-head">
          <h2>{editing ? `Ссылка №${link.id}` : 'Новая ссылка-приглашение'}</h2>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="Закрыть">✕</button>
        </header>

        {justCreated ? (
          <div className="alert ok" role="status">Ссылка создана. Скопируйте её или готовый текст ниже — по этой ссылке уже идёт учёт вступлений.</div>
        ) : null}

        {editing ? (
          <div className="link-url">
            <code>{link.url}</code>
            <button type="button" className="btn small primary" onClick={() => onCopy(link.url, 'Ссылка скопирована')}>Копировать ссылку</button>
          </div>
        ) : (
          <p className="note">Под каждую закупку — своя ссылка: по ней панель считает, сколько человек пришло и сколько осталось.</p>
        )}

        <div className="form-grid">
          <div className="span-2">
            <label htmlFor="link-name">Название (например, канал, где покупаете рекламу)</label>
            <input id="link-name" type="text" value={name} maxLength={NAME_MAX} onChange={(e) => setName(e.target.value)} autoFocus />
            {editing ? <div className="counter">В Telegram название не меняется — только в панели</div> : <div className="counter">{name.length} / {NAME_MAX}</div>}
          </div>
          <div>
            <label htmlFor="link-cost">Стоимость закупа</label>
            <input id="link-cost" type="text" inputMode="decimal" value={cost} placeholder="необязательно" onChange={(e) => setCost(e.target.value)} aria-invalid={costInvalid} />
          </div>
          <div>
            <label htmlFor="link-cur">Валюта</label>
            <input id="link-cur" type="text" value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
          </div>
          <div className="span-2">
            <label htmlFor="link-notes">Заметка (условия, контакт админа, дата выхода)</label>
            <input id="link-notes" type="text" value={notes} maxLength={1000} onChange={(e) => setNotes(e.target.value)} />
          </div>
        </div>

        <div className="editor-grid">
          <div>
            <label htmlFor="link-text">Текст рекламы — <code>{'{link}'}</code> заменится на ссылку</label>
            <textarea id="link-text" rows={9} value={adText} placeholder={'Дайджест про ИИ и IT без воды.\n\nПодписаться: {link}'} onChange={(e) => setAdText(e.target.value)} />
            <div className={`counter${adText.length > LIMIT ? ' over' : ''}`}>{adText.length} / {LIMIT}</div>
          </div>
          <div>
            <div className="label">{editing ? 'Готовый текст для админа канала' : 'Так текст получит админ канала (ссылка появится после создания)'}</div>
            <div className="preview">{renderAdText(adText, url)}</div>
            {editing ? (
              <button type="button" className="btn small primary" onClick={() => onCopy(renderAdText(adText, link.url), 'Текст скопирован')}>Копировать текст</button>
            ) : null}
          </div>
        </div>

        {!editing && !telegramEnabled ? (
          <div className="alert" role="alert">На сервере не задан TELEGRAM_BOT_TOKEN — ссылку создать нельзя.</div>
        ) : null}
        {error ? <div className="alert" role="alert">{error}</div> : null}

        <footer className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>{justCreated ? 'Готово' : 'Отмена'}</button>
          <button type="button" className={`btn${editing ? '' : ' primary'}`} onClick={save} disabled={busy || !canSave}>
            {busy ? 'Сохраняю…' : editing ? 'Сохранить изменения' : 'Сгенерировать ссылку'}
          </button>
        </footer>
      </div>
    </div>
  );
}
