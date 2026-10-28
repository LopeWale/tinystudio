import { useEffect, useState, useRef } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  RiSearchLine,
  RiMenuFoldLine,
  RiMenuUnfoldLine,
  RiLoader4Line,
  RiLogoutBoxLine,
} from "@remixicon/react";
import { Button, Modal } from "../components/studio/primitives";
import { Select, SelectItem } from "../components/boardui/base/select/select";
import { useStudio } from "./StudioContext";
import {
  destinations,
  destinationFor,
  destinationIcon,
  locationTitle,
  visibleDestinations,
} from "./navigation";
export { destinations, locationTitle } from "./navigation";
export function WorkspaceBar({
  theme,
  onTheme,
}: {
  theme: string;
  onTheme: (value: string) => void;
}) {
  const studio = useStudio(),
    location = useLocation();
  const route = location.pathname + location.search;
  const scrollPositions = useRef(new Map<string, number>());
  const [menu, setMenu] = useState<"search" | "section" | null>(null),
    [query, setQuery] = useState("");
  const searchInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (menu !== "search") return;
    const frame = requestAnimationFrame(() => searchInput.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [menu]);
  const [mobileSection, setMobileSection] = useState("Data");
  const [navFade, setNavFade] = useState(0);
  const [collapsed, setCollapsed] = useState(
    () => window.matchMedia("(max-width: 1100px)").matches,
  );
  const [narrow, setNarrow] = useState(
    () => window.matchMedia("(max-width: 760px)").matches,
  );
  const groups = visibleDestinations(studio.user?.role);
  const current = destinationFor(route),
    CurrentIcon = destinationIcon(route);
  const group = destinations.find(([, items]) =>
    items.some(([, path]) => path === current?.[1]),
  )?.[0];
  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const tablet = window.matchMedia("(max-width: 1100px)");
    const update = () => {
      setNarrow(media.matches);
      setMenu(null);
    };
    const compact = () => setCollapsed(tablet.matches);
    media.addEventListener("change", update);
    tablet.addEventListener("change", compact);
    return () => {
      media.removeEventListener("change", update);
      tablet.removeEventListener("change", compact);
    };
  }, []);
  useEffect(() => {
    scrollPositions.current.clear();
  }, [studio.scopeKey]);
  useEffect(() => {
    setMenu(null);
    setQuery("");
    const main = document.getElementById("main-content");
    const restore = requestAnimationFrame(() => {
      if (main) main.scrollTop = scrollPositions.current.get(route) || 0;
    });
    return () => {
      cancelAnimationFrame(restore);
      if (main) scrollPositions.current.set(route, main.scrollTop);
    };
  }, [route, studio.scopeKey]);
  const close = () => {
    setMenu(null);
    setQuery("");
  };
  const openSearch = () => {
    setQuery("");
    setMenu("search");
  };
  useEffect(() => {
    const find = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        if (document.querySelector('[role="dialog"]')) return;
        event.preventDefault();
        openSearch();
      }
    };
    document.addEventListener("keydown", find);
    return () => document.removeEventListener("keydown", find);
  }, []);
  const pageLinks = (search = "", section?: string) =>
    groups
      .filter((item) => !section || item.group === section)
      .map(({ group, items }) => {
        const filtered = items.filter(
          ([label]) =>
            !search ||
            `${group} ${label}`.toLowerCase().includes(search.toLowerCase()),
        );
        return (
          filtered.length > 0 && (
            <section className="navigation-group" key={group}>
              <h2>{group}</h2>
              {filtered.map(([label, path]) => {
                const Icon = destinationIcon(path);
                return (
                  <NavLink
                    key={path}
                    to={path}
                    title={collapsed && !menu ? label : undefined}
                    onClick={close}
                  >
                    <Icon size={17} aria-hidden="true" />
                    <span>{label}</span>
                  </NavLink>
                );
              })}
            </section>
          )
        );
      });
  const project = (
    <Select
      aria-label="Current project"
      selectedKey={studio.project}
      onSelectionChange={(key) => {
        if (key) studio.selectProject(String(key));
      }}
    >
      {studio.projects.map((p) => (
        <SelectItem id={p.slug} key={p.slug}>
          {p.name}
        </SelectItem>
      ))}
    </Select>
  );
  const appearance = (
    <label>
      Appearance
      <select
        aria-label="Appearance"
        value={theme}
        onChange={(event) => onTheme(event.target.value)}
      >
        <option value="light">Light</option>
        <option value="dark">Dark</option>
        <option value="system">System</option>
      </select>
    </label>
  );
  const signOut = (
    <Button
      variant="ghost"
      onClick={() =>
        studio.signOut().catch((error) => studio.toast(error.message, true))
      }
    >
      <RiLogoutBoxLine size={16} /> Sign out
    </Button>
  );
  return (
    <>
      <aside
        className={`studio-navigation${collapsed ? " is-collapsed" : ""}`}
        aria-label="Workspace sidebar"
        aria-hidden={narrow ? true : undefined}
        inert={narrow}
      >
        <div className="navigation-brand">
          <a href="#/datasets" aria-label="TinyStudio home">
            <span className="brand-mark">t</span>
            <span className="brand-name">TinyStudio</span>
          </a>
          <Button
            variant="ghost"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => setCollapsed(!collapsed)}
          >
            {collapsed ? (
              <RiMenuUnfoldLine size={17} />
            ) : (
              <RiMenuFoldLine size={17} />
            )}
          </Button>
        </div>
        <div className="navigation-project">{project}</div>
        <button
          className="navigation-search"
          onClick={openSearch}
          aria-label="Search pages"
        >
          <RiSearchLine size={16} />
          <span>Go to page</span>
          <kbd>⌘ K</kbd>
        </button>
        <div className="navigation-scroll-wrap">
          <nav
            className="navigation-scroll"
            aria-label="Main navigation"
            onScroll={(event) =>
              setNavFade(Math.min(1, event.currentTarget.scrollTop / 24))
            }
          >
            {pageLinks()}
          </nav>
          <div className="navigation-scroll-fade" aria-hidden="true">
            <i style={{ opacity: navFade }} />
            <i style={{ opacity: navFade }} />
            <i style={{ opacity: navFade }} />
          </div>
        </div>
        <div className="navigation-footer">
          <details className="workspace-account">
            <summary title={studio.user?.name || studio.user?.email}>
              <span className="account-avatar">
                {(studio.user?.name || studio.user?.email || "U")
                  .slice(0, 1)
                  .toUpperCase()}
              </span>
              <span>
                {studio.user?.name || studio.user?.email || "Account"}
              </span>
            </summary>
            <div>
              {appearance}
              {signOut}
            </div>
          </details>
        </div>
      </aside>
      <header className="workspace-bar">
        <div className="workspace-location">
          <div className="workspace-location-title">
            <CurrentIcon size={17} aria-hidden="true" />
            {group && (
              <span className="location-group">
                {group}
                <span aria-hidden="true"> / </span>
              </span>
            )}
            <strong>{locationTitle(route)}</strong>
          </div>
          <div className="workspace-global-actions">
            {narrow && (
              <button
                className="workspace-page-trigger"
                onClick={openSearch}
                aria-expanded={menu === "search"}
                aria-label="Find a page"
              >
                <RiSearchLine size={20} />
              </button>
            )}
            <Button
              variant="ghost"
              aria-label="Tasks"
              onClick={studio.showTasks}
            >
              <RiLoader4Line size={16} />
              <span>Tasks</span>
            </Button>
          </div>
        </div>
      </header>
      {narrow && (
        <nav className="mobile-section-bar" aria-label="Workspace sections">
          {groups.map((section) => {
            const Icon = destinationIcon(section.items[0]?.[1] || "");
            return (
              <button
                key={section.group}
                type="button"
                data-current={group === section.group || undefined}
                aria-expanded={
                  menu === "section" && mobileSection === section.group
                }
                onClick={() => {
                  setMobileSection(section.group);
                  setMenu("section");
                }}
              >
                <Icon size={20} aria-hidden="true" />
                <span>{section.group}</span>
              </button>
            );
          })}
        </nav>
      )}
      {menu === "section" && narrow && (
        <Modal
          open
          title={mobileSection}
          className="navigation-sheet"
          onClose={close}
        >
          <nav
            className="mobile-section-links"
            aria-label={`${mobileSection} navigation`}
          >
            {pageLinks("", mobileSection)}
          </nav>
          {mobileSection === "Workspace" && (
            <div className="mobile-workspace-options">
              <span className="muted">Current project</span>
              {project}
              <details>
                <summary>Appearance & account</summary>
                <div>
                  {appearance}
                  {signOut}
                </div>
              </details>
            </div>
          )}
        </Modal>
      )}
      {menu === "search" && (
        <Modal
          open
          title="Find a page"
          className={
            narrow ? "navigation-sheet navigation-search-sheet" : undefined
          }
          onClose={close}
        >
          <input
            ref={searchInput}
            aria-label="Find a page"
            placeholder="Search datasets, training, settings…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {query.trim() ? (
            <nav className="page-finder" aria-label="Page search results">
              {pageLinks(query.trim())}
            </nav>
          ) : (
            <p className="muted">Type a page name to find its destination.</p>
          )}
          {query.trim() &&
            !groups.some(({ group, items }) =>
              items.some(([label]) =>
                `${group} ${label}`
                  .toLowerCase()
                  .includes(query.trim().toLowerCase()),
              ),
            ) && (
              <p role="status">No pages match “{query}”. Try another name.</p>
            )}
        </Modal>
      )}
    </>
  );
}
