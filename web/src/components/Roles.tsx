import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api";
import {
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconDownload,
  IconGrip,
  IconPencil,
  IconPlus,
  IconShield,
  IconSparkle,
  IconTrash,
  IconUpload,
  IconUserPlus,
} from "../icons";
import { discordColor } from "../roles";
import { POPOSYA_COLORS, ROLE_TEMPLATES, type RoleTemplate } from "../roleTemplates";
import type {
  CommandResult,
  Guild,
  GuildRole,
  RoleInput,
  RolesView,
  SavedRoleTemplate,
} from "../types";
import { EmptyState } from "./EmptyState";
import { RolePermsEditor } from "./RolePerms";
import { SkeletonRows, SkeletonText } from "./Skeleton";
import { useToast } from "./Toast";

// битовое поле прав не влезает в number — сравниваем через BigInt.
// 0x8 = Administrator: единственное право, которое стоит подсветить даже в
// режиме просмотра (выдать роль с ним = отдать сервер).
function hasAdmin(permissions: string): boolean {
  try {
    return (BigInt(permissions) & 8n) === 8n;
  } catch {
    return false;
  }
}

function syncedAgo(iso: string | null): string {
  if (!iso) return "не синхронизировано";
  const sec = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (sec < 60) return `${sec} с назад`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин назад`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} ч назад`;
  return `${Math.floor(hr / 24)} дн назад`;
}

function toHex(c: number): string {
  return `#${c.toString(16).padStart(6, "0")}`;
}

// Перестановка порядка ровно как её сделает бот: редактируемые роли
// перераспределяются по СВОИМ же позициям (слотам), заблокированные (managed,
// @everyone, выше бота) остаются на месте. ids — редактируемые сверху вниз.
function applyEditableOrder(all: GuildRole[], ids: string[]): GuildRole[] {
  const slots = all
    .filter((r) => r.editable)
    .map((r) => r.position)
    .sort((a, b) => b - a); // сверху вниз: самый высокий слот первым
  const pos = new Map<string, number>();
  ids.forEach((id, i) => pos.set(id, slots[i]));
  return all
    .map((r) => (pos.has(r.id) ? { ...r, position: pos.get(r.id)! } : r))
    .sort((a, b) => b.position - a.position);
}

function RoleBadges({ r }: { r: GuildRole }) {
  return (
    <span className="role-badges">
      {hasAdmin(r.permissions) && (
        <span className="role-badge admin" title="Право Administrator — полный доступ">
          ADMIN
        </span>
      )}
      {r.hoist && (
        <span className="role-badge" title="Показана отдельной группой в списке участников">
          выделена
        </span>
      )}
      {r.mentionable && (
        <span className="role-badge" title="Роль можно упоминать (@)">
          @
        </span>
      )}
      {r.managed && (
        <span className="role-badge" title="Роль интеграции/бустов — Discord не даёт её менять">
          managed
        </span>
      )}
      {r.is_default && <span className="role-badge">@everyone</span>}
      {!r.editable && !r.managed && !r.is_default && (
        <span className="role-badge lock" title="Выше роли Попоси — ей недоступна">
          🔒
        </span>
      )}
    </span>
  );
}

// форма создания/правки роли. Права роли (permissions) намеренно отсутствуют —
// их редактирование появится отдельным этапом.
function RoleEditor({
  initial,
  submitLabel,
  busy,
  onSubmit,
  onCancel,
  onDelete,
  onBulk,
  holders,
}: {
  initial: RoleInput;
  submitLabel: string;
  busy: boolean;
  onSubmit: (v: RoleInput) => void;
  onCancel: () => void;
  onDelete?: () => void;
  onBulk?: (op: "assign" | "unassign") => void;
  holders?: number | null;
}) {
  const [name, setName] = useState(initial.name);
  const [colorOn, setColorOn] = useState(initial.color != null && initial.color !== 0);
  const [hex, setHex] = useState(initial.color ? toHex(initial.color) : "#8b5cf6");
  const [hoist, setHoist] = useState(initial.hoist);
  const [mentionable, setMentionable] = useState(initial.mentionable);
  const [confirmDel, setConfirmDel] = useState(false);
  const [bulkConfirm, setBulkConfirm] = useState<"assign" | "unassign" | null>(null);

  const trimmed = name.trim();

  function submit() {
    if (!trimmed) return;
    onSubmit({
      name: trimmed,
      color: colorOn ? parseInt(hex.slice(1), 16) : null,
      hoist,
      mentionable,
    });
  }

  return (
    <div className="role-editor">
      <div className="role-editor-row">
        <input
          className="input role-name-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Имя роли"
          maxLength={100}
          aria-label="Имя роли"
          autoFocus
        />
        <label className={`role-color-pick${colorOn ? "" : " off"}`} title="Цвет роли">
          <input
            type="color"
            value={hex}
            onChange={(e) => {
              setHex(e.target.value);
              setColorOn(true);
            }}
            aria-label="Цвет роли"
          />
          <span
            className="role-color-preview"
            style={{ background: colorOn ? hex : "transparent" }}
            aria-hidden="true"
          />
        </label>
        <button type="button" className="btn ghost small" onClick={() => setColorOn((v) => !v)}>
          {colorOn ? "Без цвета" : "Задать цвет"}
        </button>
      </div>

      <div className="role-presets" role="group" aria-label="Цвета Попоси">
        {POPOSYA_COLORS.map((c) => (
          <button
            key={c}
            type="button"
            className={`role-preset${colorOn && hex.toLowerCase() === c.toLowerCase() ? " active" : ""}`}
            style={{ background: c }}
            title={c}
            aria-label={`Цвет ${c}`}
            onClick={() => {
              setHex(c);
              setColorOn(true);
            }}
          />
        ))}
      </div>

      <div className="role-editor-row toggles">
        <span className="role-toggle">
          <button
            type="button"
            className={`toggle${hoist ? " on" : ""}`}
            role="switch"
            aria-checked={hoist}
            aria-label="Отдельной группой"
            onClick={() => setHoist((v) => !v)}
          >
            <span className="knob" />
          </button>
          <span className="faint small">Отдельной группой (hoist)</span>
        </span>
        <span className="role-toggle">
          <button
            type="button"
            className={`toggle${mentionable ? " on" : ""}`}
            role="switch"
            aria-checked={mentionable}
            aria-label="Можно упоминать"
            onClick={() => setMentionable((v) => !v)}
          >
            <span className="knob" />
          </button>
          <span className="faint small">Можно упоминать (@)</span>
        </span>
      </div>

      <div className="role-editor-actions">
        <button className="btn primary small" onClick={submit} disabled={busy || !trimmed}>
          <IconCheck /> {submitLabel}
        </button>
        <button className="btn ghost small" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        {onDelete &&
          (confirmDel ? (
            <span className="role-del-confirm">
              <span className="faint small">Снимет роль со всех и удалит.</span>
              <button className="btn danger small" onClick={onDelete} disabled={busy}>
                Да, удалить
              </button>
              <button
                className="btn ghost small"
                onClick={() => setConfirmDel(false)}
                disabled={busy}
              >
                Нет
              </button>
            </span>
          ) : (
            <button
              className="btn danger ghost small role-del-btn"
              onClick={() => setConfirmDel(true)}
              disabled={busy}
            >
              <IconTrash /> Удалить
            </button>
          ))}
        <span className="role-editor-hint faint small">Права роли — по кнопке щита 🛡.</span>
      </div>

      {onBulk && (
        <div className="role-bulk">
          <span className="faint small">
            Массово{typeof holders === "number" ? ` · носителей: ${holders}` : ""}:
          </span>
          {bulkConfirm ? (
            <span className="role-del-confirm">
              <span className="faint small">
                {bulkConfirm === "assign" ? "Выдать всем, у кого её нет?" : "Снять у всех носителей?"}
              </span>
              <button
                className="btn primary small"
                onClick={() => {
                  onBulk(bulkConfirm);
                  setBulkConfirm(null);
                }}
                disabled={busy}
              >
                Да
              </button>
              <button
                className="btn ghost small"
                onClick={() => setBulkConfirm(null)}
                disabled={busy}
              >
                Нет
              </button>
            </span>
          ) : (
            <>
              <button
                className="btn ghost small"
                onClick={() => setBulkConfirm("assign")}
                disabled={busy}
              >
                Выдать всем
              </button>
              <button
                className="btn ghost small"
                onClick={() => setBulkConfirm("unassign")}
                disabled={busy}
              >
                Снять у всех
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// Карточка встроенного шаблона с превью ролей и inline-подтверждением применения.
function TemplateCard({
  template,
  busy,
  onApply,
}: {
  template: RoleTemplate;
  busy: boolean;
  onApply: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  return (
    <div className="template-card">
      <div className="template-name">{template.name}</div>
      <div className="muted small template-desc">{template.description}</div>
      <div className="template-roles">
        {template.roles.map((r, i) => {
          const color = r.color ? toHex(r.color) : null;
          return (
            <span
              key={i}
              className="template-role-chip"
              style={color ? { color, borderColor: color } : undefined}
            >
              {r.name}
            </span>
          );
        })}
      </div>
      {confirm ? (
        <div className="role-panel-actions">
          <span className="faint small">Создать {template.roles.length} ролей?</span>
          <button
            className="btn primary small"
            onClick={() => {
              onApply();
              setConfirm(false);
            }}
            disabled={busy}
          >
            Да
          </button>
          <button className="btn ghost small" onClick={() => setConfirm(false)} disabled={busy}>
            Нет
          </button>
        </div>
      ) : (
        <button className="btn ghost small" onClick={() => setConfirm(true)} disabled={busy}>
          <IconPlus /> Применить
        </button>
      )}
    </div>
  );
}

// Сохранённые в БД наборы ролей: сохранить текущие под именем, применить, удалить.
// Самодостаточна; onApplied дёргает рефетч ролей в родителе (создались новые).
function SavedTemplates({ guild, onApplied }: { guild: Guild; onApplied: () => void }) {
  const [templates, setTemplates] = useState<SavedRoleTemplate[] | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDel, setConfirmDel] = useState<number | null>(null);
  const toast = useToast();

  const load = useCallback(() => {
    api
      .roleTemplates(guild.id)
      .then((v) => setTemplates(v.templates))
      .catch(() => setTemplates([]));
  }, [guild.id]);
  useEffect(load, [load]);

  async function save() {
    const n = name.trim();
    if (!n) return;
    setBusy(true);
    try {
      await api.saveRoleTemplate(guild.id, n);
      setName("");
      toast.success(`Шаблон «${n}» сохранён`);
      load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function apply(id: number) {
    setBusy(true);
    try {
      const r = await api.applyRoleTemplate(guild.id, id);
      if (r.status === "failed") toast.error(r.result ?? "Не вышло");
      else if (r.status === "done") toast.success(r.result ?? "Шаблон применён");
      else toast.info("Отправлено — применяется…");
      if (r.status !== "failed") window.setTimeout(onApplied, 1200);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    setBusy(true);
    try {
      await api.deleteRoleTemplate(guild.id, id);
      setConfirmDel(null);
      toast.success("Шаблон удалён");
      load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="saved-templates">
      <div className="role-panel-head">
        <div className="role-panel-title">Мои шаблоны</div>
      </div>
      <div className="saved-save-row">
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Имя нового шаблона из текущих ролей"
          maxLength={100}
          aria-label="Имя шаблона"
          onKeyDown={(e) => e.key === "Enter" && save()}
        />
        <button className="btn primary small" onClick={save} disabled={busy || !name.trim()}>
          <IconCheck /> Сохранить текущие
        </button>
      </div>
      {templates === null ? (
        <SkeletonText lines={2} />
      ) : templates.length === 0 ? (
        <EmptyState compact title="Пока нет сохранённых шаблонов" hint="Сохрани текущий набор ролей под именем — появится здесь." />
      ) : (
        <div className="template-grid">
          {templates.map((t) => (
            <div key={t.id} className="template-card">
              <div className="template-name">{t.name}</div>
              <div className="template-roles">
                {t.roles.map((r, i) => {
                  const color = r.color ? toHex(r.color) : null;
                  return (
                    <span
                      key={i}
                      className="template-role-chip"
                      style={color ? { color, borderColor: color } : undefined}
                    >
                      {r.name}
                    </span>
                  );
                })}
              </div>
              <div className="role-panel-actions">
                <button className="btn ghost small" onClick={() => apply(t.id)} disabled={busy}>
                  <IconPlus /> Применить
                </button>
                {confirmDel === t.id ? (
                  <span className="role-del-confirm">
                    <button
                      className="btn danger small"
                      onClick={() => remove(t.id)}
                      disabled={busy}
                    >
                      Удалить
                    </button>
                    <button
                      className="btn ghost small"
                      onClick={() => setConfirmDel(null)}
                      disabled={busy}
                    >
                      Нет
                    </button>
                  </span>
                ) : (
                  <button
                    className="btn danger ghost small"
                    onClick={() => setConfirmDel(t.id)}
                    disabled={busy}
                    aria-label={`Удалить шаблон ${t.name}`}
                    title="Удалить шаблон"
                  >
                    <IconTrash />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Автовыдача ролей новичку при входе. Самодостаточна: грузит текущий набор,
// показывает доступные боту роли переключателями, сохраняет одной кнопкой.
function AutoRoleSection({ guild, roles }: { guild: Guild; roles: GuildRole[] }) {
  const editable = roles.filter((r) => r.editable);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  useEffect(() => {
    let alive = true;
    api
      .autorole(guild.id)
      .then((v) => {
        if (!alive) return;
        setSelected(new Set(v.role_ids));
        setSaved(new Set(v.role_ids));
      })
      .catch(() => {
        /* нет доступа/пусто — оставляем пустой набор */
      });
    return () => {
      alive = false;
    };
  }, [guild.id]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const dirty =
    selected.size !== saved.size || [...selected].some((id) => !saved.has(id));

  async function save() {
    setBusy(true);
    try {
      // сохраняем в порядке списка сверху вниз; неактуальные (уехали выше бота) отсеются
      const ordered = editable.filter((r) => selected.has(r.id)).map((r) => r.id);
      const res = await api.setAutorole(guild.id, ordered);
      setSelected(new Set(res.role_ids));
      setSaved(new Set(res.role_ids));
      toast.success(ordered.length ? "Автовыдача сохранена" : "Автовыдача выключена");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="role-panel">
      <div className="role-panel-head">
        <div>
          <div className="role-panel-title">
            <IconUserPlus /> Автороли при входе
          </div>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Выбранные роли автоматически выдаются каждому новому участнику. Ботам не выдаём.
            Доступны только роли ниже линии Попоси.
          </p>
        </div>
      </div>
      {editable.length === 0 ? (
        <EmptyState compact title="Нет ролей, доступных боту для выдачи" hint="Создай роль ниже линии Попоси — её можно будет выдавать автоматически." />
      ) : (
        <div className="autorole-list">
          {editable.map((r) => {
            const color = discordColor(r.color);
            const on = selected.has(r.id);
            return (
              <button
                key={r.id}
                type="button"
                className={`autorole-item${on ? " on" : ""}`}
                role="switch"
                aria-checked={on}
                onClick={() => toggle(r.id)}
                disabled={busy}
              >
                <span
                  className={`role-swatch${color ? "" : " empty"}`}
                  style={color ? { background: color } : undefined}
                  aria-hidden="true"
                />
                <span className="autorole-name" style={color ? { color } : undefined}>
                  {r.name}
                </span>
                <span className={`toggle${on ? " on" : ""}`} aria-hidden="true">
                  <span className="knob" />
                </span>
              </button>
            );
          })}
        </div>
      )}
      <div className="role-panel-actions">
        <button className="btn primary small" onClick={save} disabled={busy || !dirty}>
          <IconCheck /> Сохранить
        </button>
        {dirty && <span className="faint small">Есть несохранённые изменения.</span>}
      </div>
    </div>
  );
}

export function Roles({ guild }: { guild: Guild }) {
  const [view, setView] = useState<RolesView | null>(null);
  const [roles, setRoles] = useState<GuildRole[]>([]);
  // копия порядка до первой перестановки; null = порядок совпадает с сервером
  const [snapshot, setSnapshot] = useState<GuildRole[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [permsId, setPermsId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  // панель инструментов: шаблоны / подтверждение импорта / автороли
  const [tool, setTool] = useState<"templates" | "import" | "autorole" | null>(null);
  const [importData, setImportData] = useState<RoleInput[] | null>(null);
  const [importError, setImportError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  const load = useCallback(() => {
    setView(null);
    setErr("");
    setSnapshot(null);
    setEditingId(null);
    setPermsId(null);
    setCreating(false);
    setTool(null);
    setImportData(null);
    setImportError("");
    api
      .roles(guild.id)
      .then((v) => {
        setView(v);
        setRoles(v.roles);
      })
      .catch((e) => setErr(e instanceof ApiError ? e.message : "Не удалось загрузить роли"));
  }, [guild.id]);

  useEffect(load, [load]);

  // мягкий рефетч без спиннера (после создания роли — подхватить её из зеркала)
  const refresh = useCallback(() => {
    api
      .roles(guild.id)
      .then((v) => {
        setView(v);
        setRoles(v.roles);
        setSnapshot(null);
      })
      .catch(() => {
        /* оставляем что есть */
      });
  }, [guild.id]);

  function report(r: CommandResult): boolean {
    if (r.status === "failed") toast.error(r.result ?? "Не вышло");
    else if (r.status === "done") toast.success(r.result ?? "Готово");
    else toast.info("Отправлено — применяется…");
    return r.status !== "failed";
  }

  const orderDirty = snapshot !== null;
  const editableIds = roles.filter((r) => r.editable).map((r) => r.id);
  const canReorder = !orderDirty ? editableIds.length >= 2 : true;
  // любой редактор открыт — порядок не трогаем
  const rowsLocked = editingId !== null || permsId !== null || creating;

  // --- порядок ---

  function commitOrder(ids: string[]) {
    setSnapshot((s) => s ?? roles);
    setRoles((rs) => applyEditableOrder(rs, ids));
  }

  function move(id: string, dir: -1 | 1) {
    const i = editableIds.indexOf(id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= editableIds.length) return;
    const ids = [...editableIds];
    [ids[i], ids[j]] = [ids[j], ids[i]];
    commitOrder(ids);
  }

  function handleDrop(targetId: string) {
    if (!dragId || dragId === targetId) {
      setDragId(null);
      setOverId(null);
      return;
    }
    const ids = editableIds.filter((id) => id !== dragId);
    const ti = ids.indexOf(targetId);
    if (ti >= 0) {
      ids.splice(ti, 0, dragId);
      commitOrder(ids);
    }
    setDragId(null);
    setOverId(null);
  }

  async function saveOrder() {
    setBusy(true);
    try {
      const ok = report(await api.reorderRoles(guild.id, editableIds));
      if (ok) setSnapshot(null);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  function cancelOrder() {
    if (snapshot) setRoles(snapshot);
    setSnapshot(null);
  }

  // --- CRUD ---

  async function doCreate(input: RoleInput) {
    setBusy(true);
    try {
      const ok = report(await api.createRole(guild.id, input));
      if (ok) {
        setCreating(false);
        window.setTimeout(refresh, 900); // дать зеркалу догнать gateway-событие
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function doEdit(role: GuildRole, input: RoleInput) {
    setBusy(true);
    try {
      const ok = report(await api.editRole(guild.id, role.id, input));
      if (ok) {
        setRoles((rs) =>
          rs.map((r) =>
            r.id === role.id
              ? {
                  ...r,
                  name: input.name,
                  color: input.color ?? 0,
                  hoist: input.hoist,
                  mentionable: input.mentionable,
                }
              : r,
          ),
        );
        setEditingId(null);
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function doBulk(role: GuildRole, op: "assign" | "unassign") {
    setBusy(true);
    try {
      report(await api.bulkRole(guild.id, role.id, op));
      window.setTimeout(refresh, 900); // носители изменились — подтянуть счётчики
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function doDelete(role: GuildRole) {
    setBusy(true);
    try {
      const ok = report(await api.deleteRole(guild.id, role.id));
      if (ok) {
        setRoles((rs) => rs.filter((r) => r.id !== role.id));
        setEditingId(null);
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  // --- экспорт / импорт / шаблоны ---

  function doExport() {
    const data = {
      version: 1,
      exported_at: new Date().toISOString(),
      guild: guild.name,
      roles: roles
        .filter((r) => r.editable)
        .map((r) => ({
          name: r.name,
          color: r.color || null,
          hoist: r.hoist,
          mentionable: r.mentionable,
        })),
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `roles-${guild.id}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`Экспортировано ролей: ${data.roles.length}`);
  }

  function onImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // сброс, чтобы повторный выбор того же файла сработал
    if (!file) return;
    setTool("import");
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result));
        const arr = Array.isArray(parsed) ? parsed : parsed?.roles;
        if (!Array.isArray(arr)) throw new Error("нет массива ролей");
        const clean: RoleInput[] = arr
          .filter((r) => r && typeof r.name === "string" && r.name.trim())
          .map((r) => ({
            name: String(r.name).trim().slice(0, 100),
            color: typeof r.color === "number" && r.color > 0 ? r.color : null,
            hoist: !!r.hoist,
            mentionable: !!r.mentionable,
          }));
        if (!clean.length) {
          setImportError("В файле нет подходящих ролей.");
          setImportData(null);
          return;
        }
        setImportError("");
        setImportData(clean);
      } catch {
        setImportError("Не удалось разобрать файл — нужен JSON от экспорта ролей.");
        setImportData(null);
      }
    };
    reader.readAsText(file);
  }

  async function applyRoles(toAdd: RoleInput[]) {
    setBusy(true);
    try {
      const ok = report(await api.importRoles(guild.id, toAdd));
      if (ok) {
        setTool(null);
        setImportData(null);
        window.setTimeout(refresh, 1200); // дать зеркалу догнать создание
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  if (err) return <div className="error-banner">{err}</div>;
  if (!view)
    return (
      <div className="card pad">
        <SkeletonRows rows={6} avatar={false} />
      </div>
    );

  const botTop = view.bot_top_position;
  // роли отсортированы сверху вниз; линия бота — перед первой ролью ниже его
  // высшей (position < botTop): всё под ней боту доступно
  const lineBefore = botTop == null ? -1 : roles.findIndex((r) => r.position < botTop);

  return (
    <div className="card pad">
      <div className="roles-head">
        <div className="h1">Роли сервера</div>
        <div className="roles-head-right">
          <span className="faint small">
            {roles.length} ролей · зеркало {syncedAgo(view.synced_at)}
          </span>
          <button
            className="btn primary small"
            onClick={() => {
              setCreating(true);
              setEditingId(null);
            }}
            disabled={orderDirty || creating || busy}
          >
            <IconPlus /> Новая роль
          </button>
        </div>
      </div>
      <p className="muted small roles-hint">
        Всё, что ниже линии Попоси, можно переименовать, перекрасить, переставить, удалить и
        настроить права (щит). Перетаскивай за ручку или жми стрелки — порядок применится по
        кнопке. Administrator панель не выдаёт, опасные права просят подтверждения.
      </p>

      <div className="role-tools">
        <button className="btn ghost small" onClick={doExport} disabled={busy}>
          <IconDownload /> Экспорт
        </button>
        <button
          className="btn ghost small"
          onClick={() => fileRef.current?.click()}
          disabled={busy || orderDirty}
        >
          <IconUpload /> Импорт
        </button>
        <button
          className={`btn ghost small${tool === "templates" ? " active" : ""}`}
          onClick={() => setTool((t) => (t === "templates" ? null : "templates"))}
          disabled={busy || orderDirty}
        >
          <IconSparkle /> Шаблоны
        </button>
        <button
          className={`btn ghost small${tool === "autorole" ? " active" : ""}`}
          onClick={() => setTool((t) => (t === "autorole" ? null : "autorole"))}
          disabled={busy}
        >
          <IconUserPlus /> Автороли
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          hidden
          onChange={onImportFile}
        />
      </div>

      {tool === "templates" && (
        <div className="role-panel">
          <div className="role-panel-title">
            <IconSparkle /> Готовые наборы ролей
          </div>
          <p className="muted small" style={{ margin: "4px 0 8px" }}>
            Создаёт недостающие роли (совпадающие по имени пропускаются). Права не переносятся —
            задай их щитом после.
          </p>
          <div className="template-grid">
            {ROLE_TEMPLATES.map((t) => (
              <TemplateCard
                key={t.key}
                template={t}
                busy={busy}
                onApply={() => applyRoles(t.roles)}
              />
            ))}
          </div>
          <SavedTemplates guild={guild} onApplied={refresh} />
        </div>
      )}

      {tool === "import" && (
        <div className="role-panel">
          <div className="role-panel-title">
            <IconUpload /> Импорт ролей из файла
          </div>
          {importError ? (
            <div className="muted small" style={{ color: "var(--danger)" }}>
              {importError}
            </div>
          ) : importData ? (
            <>
              <p className="muted small" style={{ margin: "4px 0 8px" }}>
                В файле {importData.length} ролей. Создадутся недостающие по имени, права не
                переносятся.
              </p>
              <div className="import-preview">
                {importData.map((r, i) => {
                  const color = r.color ? toHex(r.color) : null;
                  return (
                    <span key={i} className="import-chip">
                      <span
                        className={`role-swatch${color ? "" : " empty"}`}
                        style={color ? { background: color } : undefined}
                        aria-hidden="true"
                      />
                      {r.name}
                    </span>
                  );
                })}
              </div>
              <div className="role-panel-actions">
                <button
                  className="btn primary small"
                  onClick={() => applyRoles(importData)}
                  disabled={busy}
                >
                  <IconCheck /> Создать {importData.length}
                </button>
                <button
                  className="btn ghost small"
                  onClick={() => {
                    setTool(null);
                    setImportData(null);
                  }}
                  disabled={busy}
                >
                  Отмена
                </button>
              </div>
            </>
          ) : (
            <div className="muted small">Читаю файл…</div>
          )}
        </div>
      )}

      {tool === "autorole" && <AutoRoleSection guild={guild} roles={roles} />}

      {creating && (
        <RoleEditor
          initial={{ name: "", color: null, hoist: false, mentionable: false }}
          submitLabel="Создать"
          busy={busy}
          onSubmit={doCreate}
          onCancel={() => setCreating(false)}
        />
      )}

      {orderDirty && (
        <div className="order-bar">
          <span className="faint small">Порядок изменён — не сохранён.</span>
          <div className="order-bar-actions">
            <button className="btn primary small" onClick={saveOrder} disabled={busy}>
              <IconCheck /> Сохранить порядок
            </button>
            <button className="btn ghost small" onClick={cancelOrder} disabled={busy}>
              Отменить
            </button>
          </div>
        </div>
      )}

      {roles.length === 0 ? (
        <EmptyState
          title={botTop == null ? "Зеркало ещё не синхронизировано" : "Ролей нет"}
          hint={
            botTop == null
              ? "Загляни через минуту после перезапуска бота."
              : undefined
          }
        />
      ) : (
        <div className="roles-list">
          {roles.map((r, i) => {
            const color = discordColor(r.color);
            const canDrag = r.editable && !rowsLocked;
            if (editingId === r.id) {
              return (
                <div key={r.id}>
                  {i === lineBefore && (
                    <div className="bot-line">
                      <span>линия Попоси · ниже — доступно боту</span>
                    </div>
                  )}
                  <RoleEditor
                    initial={{
                      name: r.name,
                      color: r.color || null,
                      hoist: r.hoist,
                      mentionable: r.mentionable,
                    }}
                    submitLabel="Сохранить"
                    busy={busy}
                    onSubmit={(v) => doEdit(r, v)}
                    onCancel={() => setEditingId(null)}
                    onDelete={() => doDelete(r)}
                    onBulk={(op) => doBulk(r, op)}
                    holders={r.holders}
                  />
                </div>
              );
            }
            if (permsId === r.id) {
              return (
                <div key={r.id}>
                  {i === lineBefore && (
                    <div className="bot-line">
                      <span>линия Попоси · ниже — доступно боту</span>
                    </div>
                  )}
                  <RolePermsEditor
                    guildId={guild.id}
                    role={r}
                    onClose={() => setPermsId(null)}
                    onSaved={(permissions) => {
                      setRoles((rs) =>
                        rs.map((x) => (x.id === r.id ? { ...x, permissions } : x)),
                      );
                      setPermsId(null);
                    }}
                  />
                </div>
              );
            }
            return (
              <div key={r.id}>
                {i === lineBefore && (
                  <div className="bot-line">
                    <span>линия Попоси · ниже — доступно боту</span>
                  </div>
                )}
                <div
                  className={`role-row${r.editable ? "" : " locked"}${
                    overId === r.id ? " drag-over" : ""
                  }${dragId === r.id ? " dragging" : ""}`}
                  draggable={canDrag}
                  onDragStart={() => canDrag && setDragId(r.id)}
                  onDragOver={(e) => {
                    if (dragId && r.editable && r.id !== dragId) {
                      e.preventDefault();
                      setOverId(r.id);
                    }
                  }}
                  onDragLeave={() => overId === r.id && setOverId(null)}
                  onDrop={() => handleDrop(r.id)}
                  onDragEnd={() => {
                    setDragId(null);
                    setOverId(null);
                  }}
                >
                  {r.editable && canReorder && !rowsLocked ? (
                    <span className="role-grip" title="Перетащить для смены порядка" aria-hidden="true">
                      <IconGrip />
                    </span>
                  ) : (
                    <span className="role-grip empty" aria-hidden="true" />
                  )}
                  <span
                    className={`role-swatch${color ? "" : " empty"}`}
                    style={color ? { background: color } : undefined}
                    aria-hidden="true"
                  />
                  <span className="role-row-name" style={color ? { color } : undefined}>
                    {r.name}
                  </span>
                  <span className="role-holders faint small" title="носителей на сервере">
                    {r.is_default ? "—" : (r.holders ?? 0)}
                  </span>
                  <RoleBadges r={r} />
                  {r.editable && (
                    <span className="role-actions">
                      {canReorder && !rowsLocked && (
                        <>
                          <button
                            className="icon-btn"
                            onClick={() => move(r.id, -1)}
                            disabled={busy || editableIds.indexOf(r.id) === 0}
                            aria-label={`Поднять роль ${r.name}`}
                            title="Выше"
                          >
                            <IconChevronUp />
                          </button>
                          <button
                            className="icon-btn"
                            onClick={() => move(r.id, 1)}
                            disabled={busy || editableIds.indexOf(r.id) === editableIds.length - 1}
                            aria-label={`Опустить роль ${r.name}`}
                            title="Ниже"
                          >
                            <IconChevronDown />
                          </button>
                        </>
                      )}
                      <button
                        className="icon-btn"
                        onClick={() => {
                          setEditingId(r.id);
                          setPermsId(null);
                          setCreating(false);
                        }}
                        disabled={busy || orderDirty}
                        aria-label={`Редактировать роль ${r.name}`}
                        title="Редактировать"
                      >
                        <IconPencil />
                      </button>
                      <button
                        className="icon-btn"
                        onClick={() => {
                          setPermsId(r.id);
                          setEditingId(null);
                          setCreating(false);
                        }}
                        disabled={busy || orderDirty}
                        aria-label={`Права роли ${r.name}`}
                        title="Права роли"
                      >
                        <IconShield />
                      </button>
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
