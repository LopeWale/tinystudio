export interface WorkLocation { route: string; title: string; scroll: number }
/** Stores navigable locations, never page data or secret form values. */
export class NavigationSession {
  private entries: WorkLocation[] = [];
  private recent: WorkLocation[] = [];
  private key: string;
  constructor(scope: string, private storage?: Pick<Storage, 'getItem' | 'setItem'>) {
    this.key = `tinystudio.work:${scope}`;
    try {
      const saved = JSON.parse(storage?.getItem(this.key) || '{}');
      const valid = (entry: any): entry is WorkLocation => typeof entry?.route === 'string' && /^\/(?!\/)/.test(entry.route) && typeof entry.title === 'string';
      this.entries = (Array.isArray(saved.entries) ? saved.entries : []).filter(valid).slice(-8);
      this.recent = (Array.isArray(saved.recent) ? saved.recent : []).filter(valid).slice(0, 20);
    } catch { /* Storage failure must not prevent navigation. */ }
  }
  snapshot() { return { entries: this.entries.map(x=>({...x})), recent: this.recent.map(x=>({...x})) }; }
  visit(route: string, title: string) {
    const existing = this.entries.find(x=>x.route===route) || this.recent.find(x=>x.route===route);
    const entry = {route, title, scroll: existing?.scroll || 0};
    if (!this.entries.some(x=>x.route===route)) this.entries = [...this.entries.slice(-7), entry];
    this.recent = [entry, ...this.recent.filter(x=>x.route!==route)].slice(0,20);
    this.save();
  }
  close(route: string) { this.entries = this.entries.filter(x=>x.route!==route); this.save(); }
  rememberScroll(route: string, scroll: number) {
    for (const entry of [...this.entries, ...this.recent]) if(entry.route===route) entry.scroll=Math.max(0,scroll);
    this.save();
  }
  scrollFor(route: string) { return this.entries.find(x=>x.route===route)?.scroll || this.recent.find(x=>x.route===route)?.scroll || 0; }
  private save() { try { this.storage?.setItem(this.key,JSON.stringify(this.snapshot())); } catch {} }
}
