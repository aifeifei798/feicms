import React from "react";
import { siteApi, type SiteFilters, type SiteStats, type Video, type VideoSummary } from "../api";
import Player from "./Player";

const PAGE_SIZE = 24;

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
 * never leaves an empty hole in the grid. */
function Poster({ item, className = "" }: { item: VideoSummary | Video; className?: string }) {
  const url = posterUrl(item);
  return (
    <div className={`poster-frame ${className}`}>
      {url ? (
        <img
          src={url}
          alt={item.name}
          loading="lazy"
          onError={(e) => e.currentTarget.classList.add("poster-failed")}
        />
      ) : null}
      <span className="poster-fallback">{item.name}</span>
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
  const [stats, setStats] = React.useState<SiteStats | null>(null);
  const [filters, setFilters] = React.useState<SiteFilters | null>(null);

  const [q, setQ] = React.useState("");
  const [type, setType] = React.useState("");
  const [year, setYear] = React.useState("");
  const [sort, setSort] = React.useState("updated");
  const [offset, setOffset] = React.useState(0);
  const [items, setItems] = React.useState<VideoSummary[]>([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [detail, setDetail] = React.useState<Video | null>(null);
  const [related, setRelated] = React.useState<VideoSummary[]>([]);
  const [lineIndex, setLineIndex] = React.useState(0);
  const [episodeIndex, setEpisodeIndex] = React.useState(0);

  React.useEffect(() => {
    siteApi.stats().then(setStats).catch(() => setStats(null));
    siteApi.filters().then(setFilters).catch(() => setFilters(null));
  }, []);

  React.useEffect(() => {
    if (route.view !== "home") return;
    setLoading(true);
    setError(null);
    siteApi
      .videos({ q, type_name: type, year, sort, limit: PAGE_SIZE, offset })
      .then((page) => {
        setItems(page.items as VideoSummary[]);
        setTotal(page.total);
      })
      .catch((e: Error) => setError(friendlyError(e)))
      .finally(() => setLoading(false));
  }, [route, q, type, year, sort, offset]);

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

  const go = (next: Route) => {
    const path = next.view === "video" ? `/video/${next.id}` : "/";
    window.history.pushState(next, "", path);
    setRoute(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="site-shell">
      <header className="site-header">
        <a className="site-brand" href="/" onClick={(e) => { e.preventDefault(); go({ view: "home" }); }}>
          <img src="/favicon-dark.svg" alt="feicms" />
          <span>
            <strong>feicms</strong>
            <small>影视资源库</small>
          </span>
        </a>

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
              if (route.view !== "home") go({ view: "home" });
            }}
          />
          <button type="submit" className="btn btn-sm btn-primary">搜索</button>
        </form>

        <nav className="site-nav">
          <a href="/admin">管理后台</a>
        </nav>
      </header>

      {route.view === "home" ? (
        <main className="site-main">
          <section className="site-hero">
            <div>
              <h1>聚合 {stats?.sources ?? 0} 个资源站 · {stats?.videos ?? 0} 部影片</h1>
              <p className="muted">
                海报本地化 {stats?.posters ?? 0} 张
                {stats?.last_updated ? ` · 最近更新 ${formatDate(stats.last_updated)}` : ""}
              </p>
            </div>
            <div className="hero-actions">
              <select className="select select-sm" value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="updated">最近更新</option>
                <option value="name">名称</option>
                <option value="source_count">来源最多</option>
              </select>
              {filters?.years?.length ? (
                <select className="select select-sm" value={year} onChange={(e) => { setYear(e.target.value); setOffset(0); }}>
                  <option value="">全部年份</option>
                  {filters.years.map((y) => (
                    <option key={y} value={y}>{y}</option>
                  ))}
                </select>
              ) : null}
            </div>
          </section>

          <div className="chip-row">
            <button className={`chip ${type === "" ? "chip-active" : ""}`} onClick={() => { setType(""); setOffset(0); }}>
              全部
            </button>
            {(filters?.types ?? []).slice(0, 18).map((t) => (
              <button
                key={t.type_name}
                className={`chip ${type === t.type_name ? "chip-active" : ""}`}
                onClick={() => { setType(t.type_name); setOffset(0); }}
              >
                {t.type_name} <span className="chip-count">{t.count}</span>
              </button>
            ))}
          </div>

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
            {loading && <div className="muted">加载中…</div>}
          </div>

          {total > PAGE_SIZE && (
            <div className="site-pagination">
              <button className="btn btn-sm btn-outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                上一页
              </button>
              <span className="muted">第 {currentPage} / {pages} 页 · 共 {total} 部</span>
              <button
                className="btn btn-sm btn-outline"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                下一页
              </button>
            </div>
          )}
        </main>
      ) : (
        <main className="site-main detail">
          <button className="btn btn-sm btn-outline" onClick={() => go({ view: "home" })}>← 返回</button>

          {error && <div className="site-error">{error}</div>}

          {detail && (
            <div className="detail-grid">
              <div className="detail-poster">
                <Poster item={detail} className="detail-poster-img" />
                <div className="detail-meta">
                  <span className="badge">{detail.type_name || "未分类"}</span>
                  {detail.year && <span className="badge">{detail.year}</span>}
                  {detail.area && <span className="badge">{detail.area}</span>}
                  {detail.lang && <span className="badge">{detail.lang}</span>}
                  <span className="badge">{detail.plays.length} 条线路</span>
                </div>
              </div>

              <div className="detail-body">
                <h1>{detail.name}</h1>
                {detail.blurb && <p className="detail-blurb">{cleanText(detail.blurb)}</p>}
                {(detail.actor || detail.director) && (
                  <p className="muted">
                    {detail.director ? `导演：${cleanText(detail.director)} ` : ""}
                    {detail.actor ? `主演：${cleanText(detail.actor)}` : ""}
                  </p>
                )}
                <p className="muted">
                  更新 {formatDate(detail.last_updated)} · 来源 {detail.source_count} 个资源站
                </p>

                {detail.plays.length === 0 ? (
                  <div className="site-error">这条影片没有可用的播放源。</div>
                ) : (
                  <>
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

                    <Player
                      url={detail.plays[lineIndex]?.episodes[episodeIndex]?.url ?? ""}
                      poster={posterUrl(detail) || undefined}
                    />

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

                    <div className="other-lines">
                      <h2>其它线路</h2>
                      {detail.plays.map((play, index) => (
                        <div className="other-line" key={`${play.from_name}-${index}`}>
                          <span className="other-line-name">{play.from_name}</span>
                          <span className="muted">{play.episodes.length} 集</span>
                          <a href={play.episodes[0]?.url} target="_blank" rel="noopener noreferrer">在新窗口打开</a>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
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
        <a href="/admin">管理后台</a>
      </footer>
    </div>
  );
}
