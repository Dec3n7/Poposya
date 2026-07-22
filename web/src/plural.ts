// Русская множественная форма: plural(n, ["персона", "персоны", "персон"]).
// forms = [1, 2-4, 5-0/11-14]. Используется в сводках сворачиваемых блоков.
export function plural(n: number, forms: [string, string, string]): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (b > 1 && b < 5) return forms[1];
  if (b === 1) return forms[0];
  return forms[2];
}

// "6 персон", "1 персона", "2 персоны"
export const pl = (n: number, forms: [string, string, string]): string =>
  `${n} ${plural(n, forms)}`;
