import type { Metadata, Viewport } from "next";
import { AppUpdateManager } from "./components/AppUpdateManager";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "MESIL_Chat",
    template: "%s | MESIL_Chat",
  },
  description: "실버메디컬 직원 전용 메디컬 실버 채팅",
  manifest: "/mesil-chat.webmanifest",
  robots: {
    index: false,
    follow: false,
    nocache: true,
  },
  icons: {
    icon: [
      { url: "/icons/mesil-chat-192-v3.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/mesil-chat-512-v3.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [
      { url: "/icons/mesil-chat-192-v3.png", sizes: "192x192", type: "image/png" },
    ],
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "MESIL_Chat",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
  themeColor: "#0f5c56",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body>
        {children}
        <AppUpdateManager />
      </body>
    </html>
  );
}
