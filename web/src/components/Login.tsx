import { api } from "../api";

export function Login() {
  return (
    <div className="center">
      <div className="card pad login-card">
        <div className="login-mark">✂️👁🖤</div>
        <h1 className="h1">Панель Попоси</h1>
        <p className="sub">
          Управляй серверами, где она живёт. Вход только через Discord — ни логинов, ни паролей.
        </p>
        <a className="btn primary login-btn" href={api.loginUrl()}>
          Войти через Discord
        </a>
      </div>
    </div>
  );
}
