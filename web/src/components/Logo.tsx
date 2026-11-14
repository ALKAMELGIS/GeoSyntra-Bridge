/** GeoSyntra Bridge mark: a CAD vertex grip (left) and a GIS point (right) joined by the bridge — the transfer.
 *  Ink follows `currentColor`, so it works on light and dark surfaces; the arch keeps the redline accent. */
export function LogoMark({ size = 28, accent = "#b94e24", hangers = false }: { size?: number; accent?: string; hangers?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" style={{ flex: "none", display: "block" }}>
      <path d="M14 44 C 22 18, 42 18, 50 44" fill="none" stroke={accent} strokeWidth="3.6" strokeLinecap="round" />
      {hangers && <path d="M21.9 29.4 V44 M32 24.5 V44 M42.1 29.4 V44" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" opacity=".55" />}
      <path d="M6 44 H58" fill="none" stroke="currentColor" strokeWidth="3.6" strokeLinecap="round" />
      <rect x="9.5" y="39.5" width="9" height="9" rx="1.5" fill="var(--logo-bg, #FAFBFC)" stroke="currentColor" strokeWidth="2.8" />
      <circle cx="50" cy="44" r="5.4" fill="currentColor" />
    </svg>
  );
}
