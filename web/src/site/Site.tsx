import React from "react";
import { siteApi, type SiteFilters, type Video, type VideoSummary } from "../api";
import { POSTER_PLACEHOLDER, posterFallback } from "../poster";
import Player from "./Player";

const PAGE_SIZE = 30; // 6 columns x 5 rows

/** Compact page list: 1 … 4 5 6 … 20 */
function pageNumbers(current: number, pages: number): (number | "…")[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const nums: (number | "…")[] = [1];
  if (current > 3) nums.push("…");
  for (let i = Math.max(2, current - 1); i <= Math.min(pages - 1, current + 1); i += 1) nums.push(i);
  if (current < pages - 2) nums.push("…");
  nums.push(pages);
  return nums;
}

type Route = { view: "home" } | { view: "video"; id: number };

function parseRoute(path: string): Route {
  const match = path.match(/^\/video\/(\d+)/);
  if (match) return { view: "video", id: Number(match[1]) };
  return { view: "home" };
}

function posterUrl(item: VideoSummary | Video): string {
  return item.local_pic || item.pic || "";
}

/** Poster with a graceful fallback: remote hotlinks often 403, so a broken image
 * is replaced by the feicms mark instead of an empty frame. */
function Poster({ item, className = "" }: { item: VideoSummary | Video; className?: string }) {
  return (
    <div className={`poster-frame ${className}`}>
      <img
        src={posterUrl(item) || POSTER_PLACEHOLDER}
        alt={item.name}
        loading="lazy"
        onError={posterFallback}
        onLoad={(e) => {
          const img = e.currentTarget;
          const frame = img.parentElement;
          // Landscape posters look terrible cropped into a 3:4 frame: show them
          // whole on top of a blurred copy of themselves as backdrop.
          if (frame && img.naturalWidth > img.naturalHeight * 1.15) {
            frame.classList.add("poster-wide");
            frame.style.setProperty("--poster-src", `url("${img.src}")`);
          }
        }}
      />
    </div>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("zh-CN");
}

/** Upstream blurbs sometimes carry raw HTML entities. */
function cleanText(value: string): string {
  return value
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&lt;|&gt;/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function friendlyError(e: Error): string {
  if (e.message.startsWith("401")) {
    return "前台浏览已关闭（FEICMS_PUBLIC_SITE=false）。请通过 /admin 登录管理后台查看影片库。";
  }
  return e.message;
}

export default function Site() {
  const [route, setRoute] = React.useState<Route>(parseRoute(window.location.pathname));
  const [filters, setFilters] = React.useState<SiteFilters | null>(null);

  const [q, setQ] = React.useState("");
  const [semantic, setSemantic] = React.useState(false);
  const [type, setType] = React.useState("");
  const [year, setYear] = React.useState("");
  const [offset, setOffset] = React.useState(0);
  const [items, setItems] = React.useState<VideoSummary[]>([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [detail, setDetail] = React.useState<Video | null>(null);
  const [related, setRelated] = React.useState<VideoSummary[]>([]);
  const [lineIndex, setLineIndex] = React.useState(0);
  const [episodeIndex, setEpisodeIndex] = React.useState(0);
  const [pageInput, setPageInput] = React.useState("1");
  const [menu, setMenu] = React.useState<"type" | "year" | null>(null);

  React.useEffect(() => {
    siteApi.filters().then(setFilters).catch(() => setFilters(null));
  }, []);

  // Browser back/forward must drive the SPA route. Without this, pressing back
  // on a phone only rewrites the URL and the next press leaves the site.
  React.useEffect(() => {
    const onPop = (e: PopStateEvent) => {
      const state = e.state as Route | null;
      const next =
        state && typeof state === "object" && "view" in state ? state : parseRoute(window.location.pathname);
      setRoute(next);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // The nav dropdowns close on Esc or when clicking anywhere outside them.
  React.useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(null);
    };
    const onClick = (e: MouseEvent) => {
      const el = e.target as Element | null;
      if (el && !el.closest(".site-menu")) setMenu(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onClick);
    };
  }, [menu]);

  React.useEffect(() => {
    if (route.view !== "home") return;
    setLoading(true);
    setError(null);
    siteApi
      .videos({ q, type_name: type, year, limit: PAGE_SIZE, offset, semantic })
      .then((page) => {
        setItems(page.items as VideoSummary[]);
        setTotal(page.total);
      })
      .catch((e: Error) => setError(friendlyError(e)))
      .finally(() => setLoading(false));
  }, [route, q, type, year, offset, semantic]);

  React.useEffect(() => {
    if (route.view !== "video") return;
    setLoading(true);
    setError(null);
    siteApi
      .video(route.id)
      .then((video) => {
        setDetail(video);
        setLineIndex(0);
        setEpisodeIndex(0);
        document.title = `${video.name} · feicms 影视库`;
      })
      .catch((e: Error) => setError(friendlyError(e)))
      .finally(() => setLoading(false));
    siteApi
      .related(route.id)
      .then(setRelated)
      .catch(() => setRelated([]));
  }, [route]);

  React.useEffect(() => {
    if (route.view === "home") document.title = "feicms 影视库";
  }, [route]);

  // The jump box always mirrors the page actually on screen.
  React.useEffect(() => {
    setPageInput(String(Math.floor(offset / PAGE_SIZE) + 1));
  }, [offset]);

  const go = (next: Route) => {
    const path = next.view === "video" ? `/video/${next.id}` : "/";
    window.history.pushState(next, "", path);
    setRoute(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  const goToPage = (page: number) => {
    const target = Math.min(Math.max(1, Number(page) || 1), pages);
    setOffset((target - 1) * PAGE_SIZE);
  };

  return (
    <div className="site-shell">
      <header className="site-header">
        <a className="site-brand" href="/" onClick={(e) => { e.preventDefault(); go({ view: "home" }); setMenu(null); }}>
          <img src="/favicon-dark.svg" alt="feicms" />
          <span>
            <strong>feicms</strong>
            <small>影视资源库</small>
          </span>
        </a>

        {route.view === "video" && (
          <button type="button" className="detail-back" onClick={() => go({ view: "home" })}>
            ← 返回
          </button>
        )}

        <div className="site-filters">
        <div className="site-menu">
          <button
            type="button"
            className="menu-trigger"
            aria-expanded={menu === "type"}
            onClick={() => setMenu(menu === "type" ? null : "type")}
          >
            类别
            <span className="menu-value">{type || "全部"}</span>
            <span className="menu-caret">▾</span>
          </button>
          {menu === "type" && (
            <div className="menu-panel" role="menu">
              <button
                type="button"
                className={`menu-item ${type === "" ? "menu-item-active" : ""}`}
                onClick={() => { setType(""); setOffset(0); setMenu(null); }}
              >
                全部
              </button>
              {(filters?.types ?? []).map((t) => (
                <button
                  key={t.type_name}
                  type="button"
                  className={`menu-item ${type === t.type_name ? "menu-item-active" : ""}`}
                  onClick={() => {
                    setType(t.type_name);
                    setOffset(0);
                    setMenu(null);
                    if (route.view !== "home") go({ view: "home" });
                  }}
                >
                  {t.type_name} <span className="menu-count">{t.count}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <span className="filter-divider" aria-hidden="true" />

        <div className="site-menu">
          <button
            type="button"
            className="menu-trigger"
            aria-expanded={menu === "year"}
            onClick={() => setMenu(menu === "year" ? null : "year")}
          >
            年份
            <span className="menu-value">{year || "全部"}</span>
            <span className="menu-caret">▾</span>
          </button>
          {menu === "year" && (
            <div className="menu-panel" role="menu">
              <button
                type="button"
                className={`menu-item ${year === "" ? "menu-item-active" : ""}`}
                onClick={() => { setYear(""); setOffset(0); setMenu(null); }}
              >
                全部年份
              </button>
              {(filters?.years ?? []).map((y) => (
                <button
                  key={y}
                  type="button"
                  className={`menu-item ${year === y ? "menu-item-active" : ""}`}
                  onClick={() => {
                    setYear(y);
                    setOffset(0);
                    setMenu(null);
                    if (route.view !== "home") go({ view: "home" });
                  }}
                >
                  {y}
                </button>
              ))}
            </div>
          )}
        </div>
        </div>

        <form
          className="site-search"
          onSubmit={(e) => {
            e.preventDefault();
            go({ view: "home" });
            setOffset(0);
          }}
        >
          <input
            type="search"
            placeholder="搜索影片名…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
              setMenu(null);
              if (route.view !== "home") go({ view: "home" });
            }}
          />
          <button
            type="button"
            className={`search-go ${semantic ? "search-go-active" : ""}`}
            aria-label="语义搜索"
            title="语义搜索"
            onClick={() => { setSemantic((v) => !v); setOffset(0); }}
          >
            语
          </button>
          <button type="submit" className="search-go" aria-label="搜索">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-4.5-4.5" />
            </svg>
          </button>
        </form>

      </header>

      {route.view === "home" ? (
        <main className="site-main">
          {error && <div className="site-error">{error}</div>}

          <div className="poster-grid">
            {items.map((v) => (
              <article className="poster" key={v.id} onClick={() => go({ view: "video", id: v.id })}>
                <Poster item={v} />
                <div className="poster-meta">
                  <div className="poster-name">{v.name}</div>
                  <div className="poster-sub">
                    {v.type_name || "未分类"}
                    {v.year ? ` · ${v.year}` : ""}
                    {v.source_count > 1 ? ` · ${v.source_count} 条线路` : ""}
                  </div>
                  {v.remarks && <div className="poster-remarks">{cleanText(v.remarks)}</div>}
                </div>
              </article>
            ))}
            {items.length === 0 && !loading && <div className="muted">没有结果，换个关键词或分类试试。</div>}
            {loading &&
              Array.from({ length: PAGE_SIZE }).map((_, i) => (
                <div className="poster poster-skeleton" key={`skeleton-${i}`}>
                  <div className="poster-frame skeleton-frame" />
                  <div className="poster-meta">
                    <div className="skeleton-line skeleton-line-title" />
                    <div className="skeleton-line" />
                  </div>
                </div>
              ))}
          </div>

          {total > PAGE_SIZE && (
            <nav className="site-pagination" aria-label="分页">
              <button className="page-arrow" disabled={offset === 0} onClick={() => goToPage(currentPage - 1)} aria-label="上一页">
                ‹
              </button>
              {pageNumbers(currentPage, pages).map((n, i) =>
                n === "…" ? (
                  <span className="page-dots" key={`dots-${i}`}>…</span>
                ) : (
                  <button
                    key={n}
                    className={`page-num ${n === currentPage ? "page-num-active" : ""}`}
                    onClick={() => goToPage(n)}
                  >
                    {n}
                  </button>
                )
              )}
              <button className="page-arrow" disabled={currentPage >= pages} onClick={() => goToPage(currentPage + 1)} aria-label="下一页">
                ›
              </button>
              <div className="page-jump">
                <input
                  className="input input-sm page-input"
                  type="number"
                  min="1"
                  max={pages}
                  value={pageInput}
                  onChange={(e) => setPageInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      goToPage(Number(pageInput));
                    }
                  }}
                  aria-label="页码"
                />
                <span className="muted">/ {pages} 页 · 共 {total} 部</span>
              </div>
            </nav>
          )}
        </main>
      ) : (
        <main className="site-main detail">
          {error && <div className="site-error">{error}</div>}

          {detail && (
            <div className="detail-stack">
              <header className="detail-head">
                <h1>{detail.name}</h1>
                <div className="detail-meta">
                  <span className="badge">{detail.type_name || "未分类"}</span>
                  {detail.year && <span className="badge">{detail.year}</span>}
                  {detail.area && <span className="badge">{detail.area}</span>}
                  {detail.lang && <span className="badge">{detail.lang}</span>}
                  <span className="badge">{detail.source_count} 个资源站</span>
                </div>
              </header>

              {detail.plays.length === 0 ? (
                <div className="site-error">这条影片没有可用的播放源。</div>
              ) : (
                <section className="detail-player">
                  <Player
                    url={detail.plays[lineIndex]?.episodes[episodeIndex]?.url ?? ""}
                    poster={posterUrl(detail) || undefined}
                  />

                  <div className="detail-controls">
                    <div className="control-group">
                      <span className="control-label">线路</span>
                      <div className="line-tabs">
                        {detail.plays.map((play, index) => (
                          <button
                            key={`${play.from_name}-${index}`}
                            className={`line-tab ${lineIndex === index ? "line-tab-active" : ""}`}
                            onClick={() => { setLineIndex(index); setEpisodeIndex(0); }}
                          >
                            {play.from_name}
                            <span className="chip-count">{play.episodes.length}</span>
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="control-group">
                      <span className="control-label">选集</span>
                      <div className="episode-list">
                        {detail.plays[lineIndex]?.episodes.map((episode, index) => (
                          <button
                            key={`${episode.name}-${index}`}
                            className={`episode ${episodeIndex === index ? "episode-active" : ""}`}
                            onClick={() => setEpisodeIndex(index)}
                          >
                            {episode.name}
                          </button>
                        ))}
                      </div>
                    </div>

                    <span className="muted now-playing">
                      正在播放：{detail.plays[lineIndex]?.from_name} ·
                      {detail.plays[lineIndex]?.episodes[episodeIndex]?.name || "正片"}
                    </span>
                  </div>
                </section>
              )}

              <section className="detail-info">
                <div className="detail-poster">
                  <Poster item={detail} className="detail-poster-img" />
                </div>
                <div className="detail-about">
                  {detail.blurb && <p className="detail-blurb">{cleanText(detail.blurb)}</p>}
                  {(detail.actor || detail.director) && (
                    <p className="muted">
                      {detail.director ? `导演：${cleanText(detail.director)} ` : ""}
                      {detail.actor ? `主演：${cleanText(detail.actor)}` : ""}
                    </p>
                  )}
                  <p className="muted">更新 {formatDate(detail.last_updated)}</p>
                </div>
              </section>
            </div>
          )}

          {related.length > 0 && (
            <section className="related">
              <h2>同类影片</h2>
              <div className="poster-grid">
                {related.map((v) => (
                  <article className="poster" key={v.id} onClick={() => go({ view: "video", id: v.id })}>
                    <Poster item={v} />
                    <div className="poster-meta">
                      <div className="poster-name">{v.name}</div>
                      <div className="poster-sub">{v.type_name || "未分类"}{v.year ? ` · ${v.year}` : ""}</div>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}
        </main>
      )}

      <footer className="site-footer">
        <span className="muted">
          feicms · 数据来自公开资源站，本站只做聚合与检索，不托管任何视频文件。
        </span>
      </footer>
    </div>
  );
}
