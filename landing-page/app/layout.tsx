import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Takeoff AI - Automating civil engineering takeoffs from drawing",
  description: "Takeoff AI reads civil and architectural plan PDFs, extracts necessary information and turns them into Takeoff and Bill of Quantities, also, giving room for verifications through annotations.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
