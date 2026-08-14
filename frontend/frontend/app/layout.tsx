import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { JetBrains_Mono } from 'next/font/google';
import './globals.css';

/**
 * Mont Blanc and Mont are licensed fonts, not Google ones, so there is nothing
 * to download here — the family is declared in globals.css and resolves from the
 * viewer's system, falling back to Arial. Drop web font files into public/fonts
 * and add an @font-face block to serve them to everyone.
 *
 * JetBrains Mono stays for measurements and readouts: quantities need figures of
 * equal width so columns of numbers line up and digits cannot be misread.
 */
const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-jetbrains-mono',
  display: 'swap'
});

export const metadata: Metadata = {
  title: 'Takeoff AI — annotation workspace',
  description: 'Draw measured annotation layers over plan PDFs and run AI extraction.'
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className={jetbrainsMono.variable}>
      <body>{children}</body>
    </html>
  );
}
