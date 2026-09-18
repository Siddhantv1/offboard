/** An employee's nav has exactly one destination today: the FAQ chatbot.
 *  Kept as its own tiny component rather than branching inside Sidebar, since
 *  the two have nothing structural in common besides the brand mark -- an
 *  employee's case-progress view (still pending) will grow this file, not
 *  Sidebar's HR nav-group logic. */
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
        <div className="nav-item active">
          <span className="ni-icon">⁇</span>
          <span className="ni-name">Ask a question</span>
        </div>
      </nav>
    </aside>
  );
}
