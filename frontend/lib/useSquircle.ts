"use client";

import { getSvgPath } from "figma-squircle";
import { useLayoutEffect, useMemo, useRef } from "react";

export type SquircleRadii = { tl: number; tr: number; br: number; bl: number };

/** Фигмовский corner smoothing (100% = smoothing 1) через clip-path.
 *  Пересчитывается при изменении размеров элемента (в т.ч. во время анимаций).
 *  На мобильных (< 900px) отключается: там карточки прижаты к краям экрана. */
export function useSquircle<T extends HTMLElement = HTMLDivElement>(
  radius: number | Partial<SquircleRadii>,
  smoothing = 1
) {
  const ref = useRef<T | null>(null);
  const radiusKey = useMemo(() => JSON.stringify(radius), [radius]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    let frame = 0;

    const apply = () => {
      if (window.matchMedia("(max-width: 900px)").matches) {
        el.style.clipPath = "";
        return;
      }
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      if (!w || !h) {
        el.style.clipPath = "";
        return;
      }
      const r: SquircleRadii =
        typeof radius === "number"
          ? { tl: radius, tr: radius, br: radius, bl: radius }
          : { tl: 0, tr: 0, br: 0, bl: 0, ...radius };
      const path = getSvgPath({
        width: w,
        height: h,
        cornerRadius: Math.max(r.tl, r.tr, r.br, r.bl),
        topLeftCornerRadius: r.tl,
        topRightCornerRadius: r.tr,
        bottomRightCornerRadius: r.br,
        bottomLeftCornerRadius: r.bl,
        cornerSmoothing: smoothing,
        preserveSmoothing: true,
      });
      el.style.clipPath = `path('${path}')`;
    };

    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(apply);
    };

    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    window.addEventListener("resize", schedule);
    apply();
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", schedule);
      cancelAnimationFrame(frame);
      el.style.clipPath = "";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [radiusKey, smoothing]);

  return ref;
}
