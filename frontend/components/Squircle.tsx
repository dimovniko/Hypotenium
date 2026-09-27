"use client";

import { useSquircle, type SquircleRadii } from "@/lib/useSquircle";

/** Обертка с фигмовским corner smoothing 100%. */
export default function Squircle({
  radius,
  smoothing = 1,
  as,
  children,
  ...rest
}: {
  radius: number | Partial<SquircleRadii>;
  smoothing?: number;
  as?: string;
  children?: React.ReactNode;
} & React.HTMLAttributes<HTMLElement>) {
  const ref = useSquircle<HTMLElement>(radius, smoothing);
  const Tag = (as || "div") as React.ElementType;
  return (
    <Tag ref={ref} {...rest}>
      {children}
    </Tag>
  );
}
