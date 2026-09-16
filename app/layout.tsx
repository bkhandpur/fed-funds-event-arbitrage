import type { Metadata, Viewport } from "next";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://fed-funds-event-arbitrage.vercel.app"),
  title: "FOMC Basis Monitor",
  description:
    "A research monitor testing whether Kalshi FOMC probabilities and Fed Funds futures imply an executable cross-market trade.",
  openGraph: {
    title: "FOMC Basis Monitor",
    description: "Execution-aware cross-market research for Kalshi and 30-Day Fed Funds futures.",
    type: "website",
  },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#f3f1eb" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        {children}
        {process.env.VERCEL_ENV ? <Analytics /> : null}
      </body>
    </html>
  );
}
