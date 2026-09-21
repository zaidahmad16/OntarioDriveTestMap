// Quiet, real <nav>/<a> breadcrumb trail. Last item is the current page
// (not a link, per standard breadcrumb semantics).
export default function Breadcrumbs({ items }) {
  if (!items || items.length < 2) return null;
  return (
    <nav aria-label="Breadcrumb" className="breadcrumbs">
      <ol>
        {items.map((item, i) => {
          const isLast = i === items.length - 1;
          return (
            <li key={i}>
              {isLast || !item.href ? (
                <span aria-current={isLast ? "page" : undefined}>{item.name}</span>
              ) : (
                <a href={item.href} onClick={item.onClick}>
                  {item.name}
                </a>
              )}
              {!isLast && <span className="breadcrumbs__sep" aria-hidden="true">›</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
