export default function DashboardSection({
  title,
  subtitle,
  items = [],
  renderItem,
  emptyText = "No records yet.",
  children,
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p className="panel-subtitle">{subtitle}</p> : null}
        </div>
        {items.length ? <span>{items.length} items</span> : null}
      </div>
      <div className="panel-body">
        {children ? (
          children
        ) : items.length === 0 ? (
          <p className="empty-state">{emptyText}</p>
        ) : (
          items.map((item, index) => (
            <article key={`${title}-${item?.id ?? item?.task_id ?? index}`} className="card">
              {renderItem(item)}
            </article>
          ))
        )}
      </div>
    </section>
  );
}
