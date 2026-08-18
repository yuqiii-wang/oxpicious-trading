export function expiryToYyyyMm(expiryDate: string): string {
  if (!expiryDate) return "";
  const m = expiryDate.match(/^(\d{4})-(\d{2})/);
  if (m) return `${m[1]}-${m[2]}`;
  return expiryDate.slice(0, 7);
}

export function expiryCompare(a: string, b: string): number {
  return a.localeCompare(b);
}
