export const humanize = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
export const dateTime = (value: string) => new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
export function money(value: string | null, currency: string | null): string {
  if (value === null) return "Not stated";
  // Preserve the exact decimal representation; never calculate financial values using floats.
  const [whole, fraction] = value.split(".");
  return `${currency || "Currency unknown"} ${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${fraction ? "." + fraction : ""}`;
}
export const lines = (value: string) => value.split("\n").map((item) => item.trim()).filter(Boolean);
export function validateDate(value: string): string | null {
  if (!value) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number(value.slice(0, 4)) < 1) return "Enter a date using YYYY-MM-DD.";
  const parsed = new Date(value + "T00:00:00Z");
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) return "Enter a valid calendar date using YYYY-MM-DD.";
  return null;
}
export function validateMoney(value: string, currency = "USD"): string | null {
  if (!value) return null;
  if (!/^\d+(\.\d{1,2})?$/.test(value) || /^0+(\.0+)?$/.test(value)) return "Enter a positive decimal amount with at most two decimal places.";
  const [whole, decimal = ""] = value.split(".");
  if (BigInt(whole) * 100n + BigInt(decimal.padEnd(2, "0")) > 100000000000000n) return "The amount exceeds the supported limit.";
  if (currency === "JPY" && /[1-9]/.test(decimal)) return "JPY requires whole currency units.";
  return null;
}
