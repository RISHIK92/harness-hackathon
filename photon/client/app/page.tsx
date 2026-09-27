import Chrome from "./_landing/Chrome";
import Hero from "./_landing/Hero";
import TurnStrip from "./_landing/TurnStrip";
import Languages from "./_landing/Languages";
import { Closing, Features, Footer, Hiring, Rules, Sources, Statement } from "./_landing/Sections";

/**
 * Landing page. Signed-in visitors are not redirected away any more — the
 * nav and the hero CTA switch to "Dashboard" instead, so the marketing page
 * stays reachable at the root for everyone.
 */
export default function Home() {
  return (
    <main className="l-landing relative w-full overflow-x-clip">
      <Chrome />
      <Hero />
      <Statement />
      <TurnStrip />
      <Languages />
      <Hiring />
      <Features />
      <Sources />
      <Rules />
      <Closing />
      <Footer />
    </main>
  );
}
