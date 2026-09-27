"use client";

import { createContext, useContext } from "react";
import type { Chat, User } from "@/lib/types";

export type AppCtx = {
  user: User;
  chats: Chat[];
  reloadChats: () => Promise<void>;
};

export const AppContext = createContext<AppCtx | null>(null);

export function useApp(): AppCtx {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("AppContext не инициализирован");
  return ctx;
}
