import { NavLink } from "react-router-dom";

/** An employee's nav: their own case status, and the FAQ chatbot. Kept as its
 *  own tiny component rather than branching inside Sidebar, since the two
 *  have nothing structural in common besides the brand mark -- Sidebar's
 *  agent-catalogue nav-group logic has no employee equivalent. */
function Item({ to, icon, children }) {
  return (
    <NavLink to={to} className={({ isActive }) => "nav-item" + (isActive ? " active" : "")}>
      <span className="ni-icon">{icon}</span>
      <span className="ni-name">{children}</span>
    </NavLink>
  );
}

export default function EmployeeSidebar() {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">◗</span>
        <div>
          <div className="brand-name">Offboard</div>
          <div className="brand-sub">Exit Automation</div>
        </div>
      </div>

      <nav className="nav">
        <Item to="/my-case" icon="◔">
          My Case
        </Item>
        <Item to="/agents/faq_chatbot" icon="⁇">
          Ask a question
        </Item>
      </nav>
    </aside>
  );
}
