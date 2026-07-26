import { Component, type ErrorInfo, type ReactNode } from "react";

// Барьер ошибок: исключение при рендере ниже по дереву не белит всю панель, а
// показывает фолбэк с кнопкой перезагрузки. Ставится двумя ярусами — корневым
// (main.tsx, вариант center) и по-вкладочно (GuildView, key={tab}): так падение
// одной вкладки не роняет рельс навигации и сбрасывается при переключении.
export class ErrorBoundary extends Component<
  { children: ReactNode; center?: boolean },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Панель: непойманная ошибка рендера", error, info);
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    const card = (
      <div className="card pad" style={{ maxWidth: 440 }}>
        <div className="error-banner">Что-то в панели сломалось.</div>
        <button
          className="btn primary"
          style={{ marginTop: 16 }}
          onClick={() => location.reload()}
        >
          Обновить
        </button>
      </div>
    );
    return this.props.center ? <div className="center">{card}</div> : card;
  }
}
