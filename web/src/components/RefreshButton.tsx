// Единая кнопка ручного обновления read-вкладок. Тот же вид, что и ⟳ в карточке
// человека; при busy иконка крутится (кроме reduced-motion — см. styles.css).
export function RefreshButton({ onClick, busy }: { onClick: () => void; busy?: boolean }) {
  return (
    <button
      className="btn ghost small refresh-btn"
      onClick={onClick}
      disabled={busy}
      title="Обновить"
      aria-label="Обновить"
    >
      <span className={`refresh-ic${busy ? " spin" : ""}`} aria-hidden>
        ⟳
      </span>
    </button>
  );
}
