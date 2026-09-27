"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function SuperIndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/super/studies");
  }, [router]);
  return null;
}
