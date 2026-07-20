import { useCallback, useEffect, useRef, useState } from "react";

// Кроп изображения на canvas — без внешних зависимостей. Универсальный: под
// аватар (квадрат, круговой показ) и баннер (прямоугольник по правилам Discord).
// Логика «cover»: картинка всегда покрывает рамку, зум/сдвиг в её пределах.
// Экспорт в JPEG data-URL нужного размера.

const MAX_DISP = 300; // макс. ширина превью на экране (влезает в .cropper-box)

export function ImageCropper({
  outW,
  outH,
  round = false,
  title = "Обрезка изображения",
  hint,
  onDone,
  onCancel,
}: {
  outW: number;
  outH: number;
  round?: boolean;
  title?: string;
  hint?: string;
  onDone: (dataUrl: string) => void;
  onCancel: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const offset = useRef({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);

  // «cover»: минимальный масштаб, при котором картинка покрывает всю рамку
  const baseScale = img ? Math.max(outW / img.width, outH / img.height) : 1;

  const clamp = useCallback(
    (scale: number) => {
      if (!img) return;
      const w = img.width * scale;
      const h = img.height * scale;
      offset.current.x = Math.min(0, Math.max(outW - w, offset.current.x));
      offset.current.y = Math.min(0, Math.max(outH - h, offset.current.y));
    },
    [img, outW, outH],
  );

  const draw = useCallback(() => {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx || !img) return;
    const scale = baseScale * zoom;
    ctx.clearRect(0, 0, outW, outH);
    ctx.drawImage(img, offset.current.x, offset.current.y, img.width * scale, img.height * scale);
  }, [img, baseScale, zoom, outW, outH]);

  // загрузка файла
  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      const s = Math.max(outW / image.width, outH / image.height);
      offset.current = {
        x: (outW - image.width * s) / 2,
        y: (outH - image.height * s) / 2,
      };
      setZoom(1);
      setImg(image);
    };
    image.src = url;
  }

  useEffect(() => {
    if (img) draw();
  }, [img, zoom, draw]);

  // зум с сохранением центра рамки
  function onZoom(next: number) {
    if (!img) return;
    const oldScale = baseScale * zoom;
    const newScale = baseScale * next;
    const cx = (outW / 2 - offset.current.x) / oldScale;
    const cy = (outH / 2 - offset.current.y) / oldScale;
    offset.current.x = outW / 2 - cx * newScale;
    offset.current.y = outH / 2 - cy * newScale;
    clamp(newScale);
    setZoom(next);
  }

  // перетаскивание
  function onPointerDown(e: React.PointerEvent) {
    drag.current = { x: e.clientX, y: e.clientY };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }
  function onPointerMove(e: React.PointerEvent) {
    if (!drag.current || !img) return;
    const rect = canvasRef.current!.getBoundingClientRect();
    const k = outW / rect.width; // экран -> логические пиксели канваса
    offset.current.x += (e.clientX - drag.current.x) * k;
    offset.current.y += (e.clientY - drag.current.y) * k;
    drag.current = { x: e.clientX, y: e.clientY };
    clamp(baseScale * zoom);
    draw();
  }
  function onPointerUp() {
    drag.current = null;
  }

  function apply() {
    const data = canvasRef.current?.toDataURL("image/jpeg", 0.9);
    if (data) onDone(data);
  }

  // экранный размер холста: макс. ширина MAX_DISP, пропорции сохраняем
  const dispW = Math.min(MAX_DISP, outW);
  const dispH = Math.round((dispW * outH) / outW);

  return (
    <div className="cropper-overlay" role="dialog" aria-label={title}>
      <div className="cropper-box">
        {!img ? (
          <label className="btn">
            Выбрать файл…
            <input type="file" accept="image/*" hidden onChange={onFile} />
          </label>
        ) : (
          <>
            <div className="cropper-stage" style={{ width: dispW, height: dispH }}>
              <canvas
                ref={canvasRef}
                width={outW}
                height={outH}
                className="cropper-canvas"
                style={{ width: dispW, height: dispH }}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
              />
              <div className={`cropper-ring${round ? " round" : " rect"}`} />
            </div>
            <input
              type="range"
              min={1}
              max={3}
              step={0.01}
              value={zoom}
              onChange={(e) => onZoom(Number(e.target.value))}
              className="cropper-zoom"
              aria-label="Масштаб"
            />
            <p className="faint small" style={{ margin: "4px 0 0" }}>
              {hint ?? "Перетаскивай для сдвига, ползунок — масштаб."}
            </p>
          </>
        )}
        <div className="cropper-actions">
          <button className="btn ghost small" onClick={onCancel}>
            Отмена
          </button>
          {img && (
            <button className="btn primary small" onClick={apply}>
              Готово
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
