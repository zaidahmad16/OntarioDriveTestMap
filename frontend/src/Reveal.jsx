import { useEffect, useRef, useState } from "react";

// Scroll-triggered entrance for below-the-fold content (forum/discussion
// post lists) -- IntersectionObserver, not a scroll listener, and never
// gates the content's real visibility: if the observer never fires (a
// headless render, a browser without IntersectionObserver), the content
// still renders, just without the fade. Above-the-fold content (centre
// cards, route cards) uses the simpler on-mount .rise-in instead --
// nothing to "scroll into" there.
export default function Reveal({ children, delay = 0, as: Tag = "div", className, style, ...rest }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setShown(true);
      return;
    }
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true);
          obs.disconnect();
        }
      },
      { threshold: 0.1 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <Tag
      ref={ref}
      className={`reveal${shown ? " reveal--shown" : ""}${className ? ` ${className}` : ""}`}
      style={{ ...style, transitionDelay: `${delay}ms` }}
      {...rest}
    >
      {children}
    </Tag>
  );
}
