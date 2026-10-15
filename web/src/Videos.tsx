import React from "react";
import { api, type Video, type VideoSummary } from "./api";
import { POSTER_PLACEHOLDER, posterFallback } from "./poster";

type Notify = (msg: string, kind?: "success" | "error") => void;

export default function Videos({ notify }: { notify: Notify }) {
  const [q, setQ] = React.useState("");
  const [type, setType] = React.useState("");
  const [sort, setSort] = React.useState("updated");
  const [limit, setLimit] = React.useState(24);
  const [offset, setOffset] = React.useState(0);
  const [items, setItems] = React.useState<VideoSummary[]>([]);
  const [total, setTotal] = React.useState(0);
  const [types, setTypes] = React.useState<{ type_name: string; count: number }[]>([]);
  const [detail, setDetail] = React.useState<Video | null>(null);

  React.useEffect(() => {
    api.categories().then(setTypes).catch(() => setTypes([]));
  }, []);

  React.useEffect(() => {
    api
      .videos({ q, type_name: type, sort, limit, offset })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e) => notify((e as Error).message, "error"));
  }, [q, type, sort, limit, offset, notify]);

  const open = async (id: number) => {
    try {
      setDetail(await api.video(id));
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const pages = Math.max(1, Math.ceil(total / limit));

  return (
    <section>
      <div className="toolbar">
        <input
          className="input input-bordered"
          placeholder="搜索影片名"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOffset(0);
          }}
        />
        <select className="select input-bordered" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">全部分类</option>
          {types.map((t) => (
            <option key={t.type_name || "未分类"} value={t.type_name || "未分类"}>
              {t.type_name || "未分类"} ({t.count})
            </option>
          ))}
        </select>
        <select className="select input-bordered" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="updated">最近更新</option>
          <option value="name">名称</option>
          <option value="source_count">来源数</option>
        </select>
        <select className="select input-bordered" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          <option value={12}>12 条/页</option>
          <option value={24}>24 条/页</option>
          <option value={48}>48 条/页</option>
        </select>
        <span className="muted">共 {total} 条</span>
      </div>

      <div className="video-grid">
        {items.map((v) => (
          <div className="video-card" key={v.id} onClick={() => open(v.id)}>
            <img
              src={v.local_pic || v.pic || POSTER_PLACEHOLDER}
              alt={v.name}
              loading="lazy"
              onError={posterFallback}
            />
            <div className="font-semibold mt-2">{v.name}</div>
            <div className="muted">
              {v.type_name || "未分类"} · {v.year || "-"} · {v.source_count} 来源
            </div>
            <div className="muted">{v.remarks || ""}</div>
          </div>
        ))}
        {items.length === 0 && <div className="muted">没有结果</div>}
      </div>

      <div className="pagination mt-3 flex gap-2 items-center">
        <button className="btn btn-sm btn-outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
          上一页
        </button>
        <span className="muted">第 {offset / limit + 1} / {pages} 页</span>
        <button
          className="btn btn-sm btn-outline"
          disabled={offset + limit >= total}
          onClick={() => setOffset(offset + limit)}
        >
          下一页
        </button>
      </div>

      {detail && (
        <div className="modal modal-open">
          <div className="card bg-base-100 w-full max-w-2xl border border-base-300">
            <div className="card-body">
              <h2 className="card-title">{detail.name}</h2>
              <div className="muted">
                {detail.type_name} · {detail.year} · {detail.area} · {detail.lang} · 更新 {new Date(detail.last_updated).toLocaleString()}
              </div>
              {detail.blurb && <p className="mt-2">{detail.blurb}</p>}

              {detail.pic && (
                <div className="mt-2 flex gap-2 items-center">
                  <img
                    src={detail.local_pic || detail.pic}
                    alt={detail.name}
                    onError={posterFallback}
                    style={{ width: 160, height: 100, objectFit: "cover", borderRadius: "0.5rem" }}
                  />
                  <span className="muted">{detail.local_pic ? "海报已本地化" : "海报未下载（外链）"}</span>
                </div>
              )}

              <div className="mt-3">
                <h3 className="card-title">播放源（{detail.plays.length}）</h3>
                {detail.plays.map((p, i) => (
                  <div key={`${p.from_name}-${i}`} className="border border-base-300 rounded-box p-2 mt-2">
                    <div className="font-semibold">{p.from_name}</div>
                    <div className="mono muted">
                      {p.episodes.slice(0, 3).map((e) => e.url).join(" | ")}
                      {p.episodes.length > 3 && ` … 共 ${p.episodes.length} 集`}
                    </div>
                  </div>
                ))}
                {detail.plays.length === 0 && <div className="muted">无播放源</div>}
              </div>

              <div className="mt-3 flex gap-2">
                <button className="btn btn-ghost" onClick={() => setDetail(null)}>关闭</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
