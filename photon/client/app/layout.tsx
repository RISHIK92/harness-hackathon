import type { Metadata } from "next";
import { Geist, Geist_Mono, Instrument_Serif } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

/** Editorial display face — used for the wordmark and the big italic lines. */
const instrumentSerif = Instrument_Serif({
  variable: "--font-display",
  weight: "400",
  style: ["normal", "italic"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Photon — the employee who has already read everything",
  description:
    "Photon joins your customer calls having read every repo, doc, Slack thread and ticket you connect, and answers in about a second and a half — with a citation behind every claim. English, Hindi, Telugu and Tamil.",
};

/** Development only: errors thrown by browser EXTENSIONS never reach Next's
 * error overlay.
 *
 * Extensions inject scripts into every page, and when one fails — MetaMask's
 * inpage.js rejecting with "Failed to connect to MetaMask" on a page that has
 * no wallet code at all is the usual one — the dev overlay reports it as if
 * the app had crashed. This runs before Next's own handlers (inline, in
 * <head>) and stops only events whose source or stack is an extension URL,
 * so every error from Photon's own code still surfaces exactly as before.
 * Production has no overlay, so nothing is injected there.
 */
const IGNORE_EXTENSION_ERRORS = `(() => {
  const ext = /(chrome|moz|safari-web)-extension:\\/\\//;
  const fromExtension = (e) => {
    const r = e.reason;
    const text = [e.filename, e.error && e.error.stack, r && r.stack, typeof r === "string" ? r : ""].join("\\n");
    return ext.test(text);
  };
  const swallow = (e) => { if (fromExtension(e)) { e.stopImmediatePropagation(); e.preventDefault(); } };
  window.addEventListener("error", swallow, true);
  window.addEventListener("unhandledrejection", swallow, true);
})();`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${instrumentSerif.variable} h-full antialiased`}
    >
      {process.env.NODE_ENV !== "production" && (
        <head>
          <script dangerouslySetInnerHTML={{ __html: IGNORE_EXTENSION_ERRORS }} />
        </head>
      )}
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
