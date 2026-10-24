export function histogram(h, color = "var(--s1)", width = 320, height = 90, dataContext = () => "") {
  if (!h || !h.counts || !h.counts.length) return `<div class="muted">no data</div>`;
  const max = Math.max(...h.counts, 1), n = h.counts.length, gap = 2, bw = (width - gap * (n - 1)) / n;
  const bars = h.counts.map((c, i) => `<rect x="${(i * (bw + gap)).toFixed(1)}" y="${(height - 14 - (c / max) * (height - 18)).toFixed(1)}" width="${bw.toFixed(1)}" height="${((c / max) * (height - 18)).toFixed(1)}" rx="2" fill="${color}"><title>${h.bins[i]}–${h.bins[i + 1] ?? h.bins[i]}: ${c}</title></rect>`).join("");
  return `<svg ${dataContext("Histogram bins and counts", h)} viewBox="0 0 ${width} ${height}" style="width:100%;height:${height}px">${bars}<text x="0" y="${height - 2}" font-size="10" fill="var(--text-3)" font-family="var(--mono)">${h.bins[0]}</text><text x="${width}" y="${height - 2}" font-size="10" fill="var(--text-3)" text-anchor="end" font-family="var(--mono)">${h.bins[h.bins.length - 1]}</text></svg>`;
}
