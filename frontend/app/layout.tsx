import type { Metadata, Viewport } from "next";

export const metadata: Metadata = {
  title: "Fantasy Football Copilot",
  description: "Lineup and FAAB recommendations you can interrogate.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily: "ui-sans-serif, system-ui, -apple-system, sans-serif",
          lineHeight: 1.5,
        }}
      >
        <main style={{ maxWidth: 880, margin: "0 auto", padding: "24px 16px" }}>{children}</main>
      </body>
    </html>
  );
}
