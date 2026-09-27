"use client";

import { getSvgPath } from "figma-squircle";
import { useEffect } from "react";

const SELECTOR = ".btn, .btn-send";
const RADIUS = 6;

/** Глобальный corner smoothing для кнопок: следит за DOM и вешает
 *  фигмовский squircle-клип на все кнопки, включая новые. */
export default function SquircleButtons() {
  useEffect(() => {
    const observers = new Map<HTMLElement, ResizeObserver>();

    const apply = (el: HTMLElement) => {
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      if (!w || !h) return;
      const path = getSvgPath({
        width: w,
        height: h,
        cornerRadius: RADIUS,
        cornerSmoothing: 1,
        preserveSmoothing: true,
      });
      el.style.clipPath = `path('${path}')`;
    };

    const attach = (el: HTMLElement) => {
      if (observers.has(el)) return;
      const ro = new ResizeObserver(() => apply(el));
      ro.observe(el);
      observers.set(el, ro);
      apply(el);
    };

    let frame = 0;
    const scan = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        document.querySelectorAll<HTMLElement>(SELECTOR).forEach(attach);
        for (const [el, ro] of observers) {
          if (!el.isConnected) {
            ro.disconnect();
            observers.delete(el);
          }
        }
      });
    };

    scan();
    const mo = new MutationObserver(scan);
    mo.observe(document.body, { childList: true, subtree: true });
    return () => {
      mo.disconnect();
      observers.forEach((ro) => ro.disconnect());
      cancelAnimationFrame(frame);
    };
  }, []);

  return null;
}
