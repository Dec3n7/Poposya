import { useCallback, useEffect, useRef, useState } from "react";

// Квадратный кроп аватара по правилам Discord (1:1, круговой показ). Свой на
// canvas — без внешних зависимостей. Экспорт в JPEG data-URL.
const SIZE = 512;

export function AvatarCropper({
  onDone,
  onCancel,
}: {
  onDone: (dataUrl: string) => void;
  onCancel: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const offset = useRef({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);

  const baseScale = img ? SIZE / Math.min(img.width, img.height) : 1;

  const clamp = useCallback(
    (scale: number) => {
      if (!img) return;
      const w = img.width * scale;
      const h = img.height * scale;
      offset.current.x = Math.min(0, Math.max(SIZE - w, offset.current.x));
      offset.current.y = Math.min(0, Math.max(SIZE - h, offset.current.y));
    },
    [img],
  );

  const draw = useCallback(() => {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx || !img) return;
    const scale = baseScale * zoom;
    ctx.clearRect(0, 0, SIZE, SIZE);
    ctx.drawImage(img, offset.current.x, offset.current.y, img.width * scale, img.height * scale);
  }, [img, baseScale, zoom]);

  // загрузка файла
  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      const s = SIZE / Math.min(image.width, image.height);
      offset.current = {
        x: (SIZE - image.width * s) / 2,
        y: (SIZE - image.height * s) / 2,
      };
      setZoom(1);
      setImg(image);
    };
    image.src = url;
  }

  useEffect(() => {
    if (img) draw();
  }, [img, zoom, draw]);

  // зум с сохранением центра
  function onZoom(next: number) {
    if (!img) return;
    const oldScale = baseScale * zoom;
    const newScale = baseScale * next;
    const cx = (SIZE / 2 - offset.current.x) / oldScale;
    const cy = (SIZE / 2 - offset.current.y) / oldScale;
    offset.current.x = SIZE / 2 - cx * newScale;
    offset.current.y = SIZE / 2 - cy * newScale;
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
    const k = SIZE / rect.width; // экран -> логические пиксели канваса
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

  return (
    <div className="cropper-overlay" role="dialog" aria-label="Обрезка аватара">
      <div className="cropper-box">
        {!img ? (
          <label className="btn">
            Выбрать файл…
            <input type="file" accept="image/*" hidden onChange={onFile} />
          </label>
        ) : (
          <>
            <div className="cropper-stage">
              <canvas
                ref={canvasRef}
                width={SIZE}
                height={SIZE}
                className="cropper-canvas"
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
              />
              <div className="cropper-ring" />
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
              Перетаскивай для сдвига, ползунок — масштаб. Кружком показано, как увидит Discord.
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
