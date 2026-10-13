import type { Metadata } from "next";

import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { ApiBanner, StatusProvider } from "@/components/Status";

export const metadata: Metadata = {
  title: "Relay - AI automations on n8n",
  description: "Operations console for Relay: AI business automations on self-hosted n8n, with human review, dead letters and evaluation.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <StatusProvider>
          <div className="flex min-h-screen flex-col lg:flex-row">
            <Sidebar />
            <main className="min-w-0 flex-1">
              <ApiBanner />
              {children}
            </main>
          </div>
        </StatusProvider>
      </body>
    </html>
  );
}
