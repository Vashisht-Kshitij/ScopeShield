import { ContractWorkspace } from "@/components/contract-workspace";
export default async function Page({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ tab?: string }> }) {
  const [{ id }, query] = await Promise.all([params, searchParams]);
  return <ContractWorkspace key={`${id}-${query.tab || "milestones"}`} id={id} initialTab={query.tab} />;
}
