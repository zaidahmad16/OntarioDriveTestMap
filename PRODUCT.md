# Product

## Register

product

## Users

Ontario drivers preparing for a G or G2 road test, mostly first-time or anxious test-takers looking for the real streets and turns their local DriveTest centre uses. Used in short, focused sessions: before a test to study the route, or in the car itself (passenger-read, or reviewed beforehand) while practicing. Also a small population of contributors (people who've recently taken or driven a route) submitting what they experienced, and centre-specific communities swapping tips in the forum/discussion sections.

Context: often on a phone, often stressed about an upcoming test, sometimes in a moving vehicle. Low tolerance for confusion or clutter; high need for "can I trust this."

## Product Purpose

A crowdsourced map of real Ontario DriveTest routes, built from actual GPS traces, hand-transcribed drive-test videos, and community submissions, not schematic guesses or paywalled placeholders (which is what every existing competitor offers, confirmed directly by auditing them). Success looks like: a test-taker opens their centre, sees the routes that are actually driven, and leaves confident about what streets and turns to expect, with GPS-verified data and honestly-labeled confidence (confirmed vs. predicted vs. below-threshold) at every layer.

## Brand Personality

Three words: **grounded, precise, unpretentious.** This is a civic-utility tool, not a startup pitch. It should feel like it was built by someone who actually drove these routes and cared about getting them exactly right, not like a SaaS product trying to look impressive. Calm and legible under stress (a nervous test-taker checking a route the night before, or a passenger reading turns aloud in a moving car) beats slick or playful.

## Anti-references

- The exact competitors already audited: template-clone booking-bot sites (ontariodrivetestroutes.com, drivetestroutescanada.com) that show a disclaimed fake "sample route" for free and gate the real one behind a purchase; drgo.ca's explicitly "schematic, not to scale" map synthesized from anecdotes. This product's entire reason to exist is being the opposite of those: real GPS data, openly shown, honestly labeled.
- Generic SaaS/dashboard chrome: gradient hero banners, big glossy stat cards, marketing-site polish applied to what is actually a utility tool. This is not a product being sold; it's a tool being used, often under time pressure.
- Anything that makes predicted/inferred route data look as certain as confirmed data. This is a hard, previously-litigated line (a driving-test candidate could physically execute a turn no one ever confirmed) and it must never be softened for visual cleanliness.

## Design Principles

1. **Confidence is data, not decoration.** Trust tiers (confirmed / predicted / below-threshold) are a first-class visual signal everywhere a route is shown, not a footnote. Never let a cleaner-looking UI blur that line.
2. **Built for the moment of use, not a demo.** Design for a phone screen, a passenger reading aloud, or a nervous test-taker skimming quickly at night, not for a wide desktop screenshot.
3. **Show real texture, not synthetic polish.** Real street names, real junction photos/descriptions, real vote counts and source counts are more trustworthy-looking than smoothed-over stock UI, because they ARE the product's actual advantage over every competitor.
4. **Calm under pressure.** Low visual noise, generous legibility, no urgency-manufacturing patterns (fake scarcity, aggressive CTAs). The user is already anxious about a driving test; the interface shouldn't add to that.
5. **Utility first, delight where it's earned.** Motion and personality are welcome in service of clarity (e.g. a live GPS marker moving smoothly, a satisfying quiz-mode correct/incorrect state) but never at the cost of a slower, harder-to-parse core task.

## Accessibility & Inclusion

Standard WCAG AA target. Real-world constraints that matter here specifically: used one-handed on a phone, sometimes in a moving vehicle (passenger-read, or reviewed pre-drive, never dashboard-glanced while actually driving, per the app's own existing drive-along disclaimer), sometimes by non-native English speakers (French i18n already exists and should extend to the new UI), and by people under genuine test-day anxiety, so clear contrast, generous tap targets, and unambiguous state (confirmed vs. predicted, on-route vs. off-route) all matter more than usual.
