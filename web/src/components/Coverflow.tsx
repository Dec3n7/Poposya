import { useEffect, useRef } from "react";

import { roleColor } from "../roles";
import type { LeaderEntry } from "../types";

// Крутящийся коверфлоу-подиум: три карточки, центральная ближе и крупнее, боковые
// меньше и позади. Масштаб/глубина/прозрачность — приподнятый косинус (гладко, без
// «стука» на пике). Ховер плавно и сильно замедляет (не останавливает). Уважает
// prefers-reduced-motion (тогда стоит статично). Идентичность несёт цвет тира.
export function Coverflow({ entries }: { entries: LeaderEntry[] }) {
  const stageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const cards = Array.from(stage.querySelectorAll<HTMLElement>(".pod3"));
    const N = cards.length;
    if (N === 0) return;

    const SPREAD = 150; // px между центром и боковой
    const BASE = 0.32; // карточек/сек
    const W = 1.9; // видимый радиус в карточках
    let pos = 0;
    let speed = BASE;
    let hover = false;
    let last = performance.now();
    let raf = 0;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

    const onEnter = () => (hover = true);
    const onLeave = () => (hover = false);
    stage.addEventListener("mouseenter", onEnter);
    stage.addEventListener("mouseleave", onLeave);

    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const target = hover ? BASE * 0.05 : BASE; // сильно замедлить, но не в ноль
      speed += (target - speed) * (1 - Math.pow(0.2, dt)); // плавный ease к target
      if (!reduce) pos = (pos + speed * dt) % N;
      for (let i = 0; i < N; i++) {
        let d = (((i - pos) % N) + N) % N;
        if (d > N / 2) d -= N; // ближайшая копия слева/справа
        const ad = Math.abs(d);
        const el = cards[i];
        if (ad >= W) {
          el.style.opacity = "0";
          el.style.zIndex = "0";
          el.style.pointerEvents = "none";
          continue;
        }
        // приподнятый косинус: 1 в центре, 0 на краю, производная в центре = 0
        const f = (1 + Math.cos((Math.PI * d) / W)) / 2;
        el.style.pointerEvents = ad < 0.5 ? "auto" : "none";
        el.style.opacity = f.toFixed(3);
        el.style.zIndex = String(Math.round(f * 100));
        const scale = 0.62 + 0.38 * f;
        el.style.transform =
          `translate(-50%,-50%) translateX(${(d * SPREAD).toFixed(1)}px) ` +
          `translateZ(${(-(1 - f) * 70).toFixed(1)}px) scale(${scale.toFixed(3)})`;
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      stage.removeEventListener("mouseenter", onEnter);
      stage.removeEventListener("mouseleave", onLeave);
    };
  }, [entries]);

  return (
    <div className="pod-stage" ref={stageRef}>
      {entries.map((e) => {
        const c = roleColor(e.role_index);
        const name = e.username ?? `ID ${e.user_id}`;
        const ring = `color-mix(in srgb, ${c} 45%, transparent)`;
        return (
          <div className="pod3" key={e.user_id}>
            <span
              className="pod3-av"
              style={{ boxShadow: `0 0 0 3px ${ring}`, background: e.avatar ? "transparent" : c }}
            >
              {e.avatar ? <img src={e.avatar} alt="" /> : name.slice(0, 1).toUpperCase()}
            </span>
            <div className="pod3-name">{name}</div>
            <div className="pod3-role" style={{ color: c }}>
              {e.role ?? "без роли"}
              {e.is_exclusive ? " · 🖤" : ""}
            </div>
            <div className="pod3-pts">{e.points.toLocaleString("ru")}</div>
          </div>
        );
      })}
    </div>
  );
}
