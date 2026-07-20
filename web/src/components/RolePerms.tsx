import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../api";
import { IconCheck } from "../icons";
import type { CommandResult, GuildRole, PermCatalog } from "../types";

// Редактор прав роли. Работаем битовым полем через BigInt (не влезает в number).
// Ограждения — настоящие — на стороне бота; здесь UX-подсказки: недоступные боту
// права гасим, Administrator показываем замком (панель его не выдаёт), включение
// опасного права требует подтверждения.
export function RolePermsEditor({
  guildId,
  role,
  onClose,
  onSaved,
}: {
  guildId: string;
  role: GuildRole;
  onClose: () => void;
  onSaved: (permissions: string) => void;
}) {
  const [cat, setCat] = useState<PermCatalog | null>(null);
  const [err, setErr] = useState("");
  const original = useMemo(() => BigInt(role.permissions), [role.permissions]);
  const [value, setValue] = useState<bigint>(original);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    api
      .rolePermissions(guildId)
      .then(setCat)
      .catch((e) =>
        setErr(e instanceof ApiError ? e.message : "Не удалось загрузить каталог прав"),
      );
  }, [guildId]);

  const botMask = cat ? BigInt(cat.bot_mask) : 0n;
  const adminBit = cat ? BigInt(cat.admin_bit) : 8n;
  const hasAdmin = (original & adminBit) === adminBit;

  function toggle(bit: bigint) {
    setValue((v) => ((v & bit) === bit ? v & ~bit : v | bit));
    setConfirming(false);
    setMsg(null);
  }

  // опасные права, которые это сохранение включит впервые (для подтверждения)
  const newlyDangerous = useMemo(() => {
    if (!cat) return [] as string[];
    const out: string[] = [];
    for (const c of cat.categories) {
      for (const p of c.perms) {
        const bit = BigInt(p.bit);
        if (p.dangerous && (value & bit) === bit && (original & bit) !== bit) out.push(p.label);
      }
    }
    return out;
  }, [cat, value, original]);

  const dirty = value !== original;

  function report(r: CommandResult): boolean {
    if (r.status === "failed") setMsg(r.result ?? "Не вышло");
    else if (r.status === "done") setMsg(r.result ?? "Готово");
    else setMsg("Отправлено — применяется…");
    return r.status !== "failed";
  }

  async function save() {
    if (!dirty) {
      onClose();
      return;
    }
    // первый клик по «Сохранить» при новых опасных правах — показать подтверждение
    if (newlyDangerous.length > 0 && !confirming) {
      setConfirming(true);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const ok = report(await api.setRolePermissions(guildId, role.id, String(value)));
      if (ok) onSaved(String(value));
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <div className="perms-editor">
      <div className="perms-head">
        <span className="faint">
          Права роли «{role.name}»
        </span>
        {msg && <span className="faint small">{msg}</span>}
      </div>

      {err ? (
        <div className="muted small">{err}</div>
      ) : !cat ? (
        <div className="muted small">Загрузка…</div>
      ) : (
        <>
          {hasAdmin && (
            <div className="perms-admin-lock">
              🛡 У роли есть <b>Administrator</b> — она может всё. Панель этот бит не меняет
              (только через Discord). Тумблеры ниже на неё фактически не влияют.
            </div>
          )}

          {cat.categories.map((c) => (
            <div className="perms-cat" key={c.key}>
              <div className="perms-cat-head faint small">{c.label}</div>
              <div className="perms-grid">
                {c.perms.map((p) => {
                  const bit = BigInt(p.bit);
                  const on = (value & bit) === bit;
                  const manageable = (botMask & bit) === bit;
                  return (
                    <button
                      key={p.name}
                      type="button"
                      role="switch"
                      aria-checked={on}
                      className={`perm-item${p.dangerous ? " danger" : ""}${
                        manageable ? "" : " locked"
                      }`}
                      disabled={busy || !manageable}
                      onClick={() => toggle(bit)}
                      title={
                        manageable
                          ? p.dangerous
                            ? "Опасное право — включение попросит подтверждение"
                            : undefined
                          : "Недоступно самому боту — выдать нельзя"
                      }
                    >
                      <span className={`toggle${on ? " on" : ""}`} aria-hidden="true">
                        <span className="knob" />
                      </span>
                      <span className="perm-label">
                        {p.dangerous && <span className="perm-warn" aria-hidden="true">⚠</span>}
                        {p.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}

          {confirming && newlyDangerous.length > 0 && (
            <div className="perms-confirm">
              <span className="small">
                Включаешь опасные права: <b>{newlyDangerous.join(", ")}</b>. Подтверди.
              </span>
            </div>
          )}

          <div className="perms-actions">
            <button
              className={`btn small ${confirming ? "danger" : "primary"}`}
              onClick={save}
              disabled={busy || !dirty}
            >
              <IconCheck /> {confirming ? "Да, включить и сохранить" : "Сохранить права"}
            </button>
            <button className="btn ghost small" onClick={onClose} disabled={busy}>
              Отмена
            </button>
          </div>
        </>
      )}
    </div>
  );
}
