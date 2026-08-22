import { useRovingTabIndex } from "../../hooks/useRovingTabIndex";

interface TabItem {
  id: string;
  label: string;
}

interface TabsProps {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  "aria-label": string;
  className?: string;
}

export function Tabs({ items, value, onChange, "aria-label": ariaLabel, className = "primary-tabs" }: TabsProps) {
  const { setItemRef, handleKeyDown } = useRovingTabIndex({
    active: true,
    itemCount: items.length,
    onActivate: (index) => onChange(items[index].id),
  });

  return (
    <div className={className} role="tablist" aria-label={ariaLabel}>
      {items.map((item, index) => (
        <button
          key={item.id}
          id={`tab-${item.id}`}
          ref={setItemRef(index)}
          role="tab"
          aria-selected={value === item.id}
          aria-controls={`panel-${item.id}`}
          tabIndex={value === item.id ? 0 : -1}
          onClick={() => onChange(item.id)}
          onKeyDown={(event) => handleKeyDown(event, index)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
