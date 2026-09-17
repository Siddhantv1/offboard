import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="home-head">
      <h1>No such page</h1>
      <p>
        That address does not match an agent or a view. <Link to="/">Back to all agents</Link>.
      </p>
    </div>
  );
}
