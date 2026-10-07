import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VoiceMem",
  description: "Production web interface for VoiceMem",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
