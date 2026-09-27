import type { Metadata } from "next";
import { Golos_Text } from "next/font/google";
import SquircleButtons from "@/components/SquircleButtons";
import "./globals.css";

const golos = Golos_Text({
  subsets: ["cyrillic", "latin"],
  weight: ["400", "500", "600", "800"],
  display: "swap",
  // Эмодзи-фолбэк: без него класс next/font перебивает font-family из globals.css
  // и эмодзи (⚠️ и т.п.) рендерятся черно-белыми глифами
  fallback: [
    "-apple-system",
    "Segoe UI",
    "Arial",
    "sans-serif",
    "Apple Color Emoji",
    "Segoe UI Emoji",
    "Noto Color Emoji",
  ],
});

export const metadata: Metadata = {
  title: "гипотениум",
  description: "Чат с базой исследований",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className={golos.className}>
        <SquircleButtons />
        {children}
      </body>
    </html>
  );
}
