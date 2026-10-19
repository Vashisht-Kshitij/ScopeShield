import Link from "next/link";
export default function NotFound() { return <div className="empty-state"><h1>This page is not in the workspace</h1><p><Link className="text-button" href="/contracts">Return to your agreements</Link></p></div>; }
