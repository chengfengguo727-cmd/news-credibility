import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "News Credibility Tracker",
  description: "How well do financial news sources predict the market?",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="mx-auto max-w-5xl px-4 py-8">
          <header className="mb-8 flex items-baseline justify-between">
            <h1 className="text-2xl font-semibold tracking-tight">News Credibility</h1>
            <nav className="text-sm text-neutral-500">
              <a href="/" className="hover:underline">Sources</a>
            </nav>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
