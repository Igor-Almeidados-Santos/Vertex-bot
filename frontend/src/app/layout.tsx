import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vertex-bot | Dashboard Quântico",
  description: "Painel de controle em tempo real e monitoramento automatizado do Vertex-bot",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR" className="dark">
      <body className="min-h-screen bg-background text-foreground antialiased selection:bg-brand-500/30 selection:text-brand-300">
        {children}
      </body>
    </html>
  );
}

