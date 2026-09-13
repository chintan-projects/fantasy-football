import Link from "next/link";

export default function Home() {
  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 4 }}>Fantasy Football Copilot</h1>
      <p style={{ color: "#555", marginTop: 0 }}>Two decisions a week. Two minutes.</p>
      <ul>
        <li>
          <Link href="/lineup">Lineup — who to start this week</Link>
        </li>
        <li>
          <Link href="/waivers">Waivers — what to bid, and how much</Link>
        </li>
      </ul>
    </div>
  );
}
