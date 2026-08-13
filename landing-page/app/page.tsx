import { Capabilities } from '../components/capabilities';
import { Cta } from '../components/cta';
import { Features } from '../components/features';
import { Hero } from '../components/hero';
import { HowItWorks } from '../components/how-it-works';
import { SiteFooter } from '../components/site-footer';
import { SiteHeader } from '../components/site-header';

export default function Page() {
  return (
    <>
      <SiteHeader />
      <main>
        <Hero />
        <HowItWorks />
        <Features />
        <Capabilities />
        <Cta />
      </main>
      <SiteFooter />
    </>
  );
}
