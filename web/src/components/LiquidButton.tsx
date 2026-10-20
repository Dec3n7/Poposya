import type { ReactNode } from "react";

type LiquidButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  /** Если задан — рендерится <a> вместо <button> (внешний переход, OAuth). */
  href?: string;
  variant?: "default" | "primary" | "danger";
  size?: "md" | "sm";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
  title?: string;
  ariaLabel?: string;
};

// Кнопка в стиле Liquid Glass (секция «liquid glass button» в styles.css):
// стеклянная призма с бегущим бликом по кромке, conic-кант и объёмная тень
// отдельным слоем. Всё масштабируется через font-size (em-скейлинг) — размер
// задаётся классом (.small) или font-size на самом элементе (.login-btn).
// children — только текст или текст+иконка: блик-оверлей span::after копирует
// размер спана, сложная вёрстка внутри поедет.
export function LiquidButton({
  children,
  onClick,
  href,
  variant = "default",
  size = "md",
  disabled,
  type = "button",
  className,
  title,
  ariaLabel,
}: LiquidButtonProps) {
  const cls = [
    "btn-lq",
    variant !== "default" ? variant : null,
    size === "sm" ? "small" : null,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  const inner = <span>{children}</span>;
  const shadow = <span className="btn-lq-shadow" aria-hidden />;

  if (href !== undefined) {
    return (
      <span className="btn-lq-wrap">
        <a className={cls} href={href} title={title} aria-label={ariaLabel}>
          {inner}
        </a>
        {shadow}
      </span>
    );
  }

  return (
    <span className="btn-lq-wrap">
      <button
        className={cls}
        onClick={onClick}
        disabled={disabled}
        type={type}
        title={title}
        aria-label={ariaLabel}
      >
        {inner}
      </button>
      {shadow}
    </span>
  );
}
