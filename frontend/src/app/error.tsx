"use client";
import { Button } from "@/components/ui";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return <div className="panel empty-state" role="alert"><h1>The workspace could not be displayed</h1><p>Your saved backend records are unchanged. Try reloading this view.</p><Button onClick={reset}>Reload workspace</Button></div>;
}
