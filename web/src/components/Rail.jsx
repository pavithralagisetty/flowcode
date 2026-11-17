import { FileText, House, Settings2 } from "lucide-react";

const ITEMS = [
  { id: "home", label: "Home", icon: House },
  { id: "reports", label: "Reports", icon: FileText },
  { id: "settings", label: "Manage", icon: Settings2 },
];

export default function Rail({ section, go }) {
  return (
    <nav className="rail">
      {ITEMS.map(({ id, label, icon: Icon }) => (
        <button key={id} className={`rail-item ${section === id ? "active" : ""}`} onClick={() => go(`/${id}`)}>
          <Icon size={24} strokeWidth={section === id ? 2.2 : 1.6} />
          {label}
        </button>
      ))}
    </nav>
  );
}
