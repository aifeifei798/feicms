import React from "react";
import { api, type DownloadProgress, type ImageLayout, type ImageRecord, type ImageStats } from "./api";
import { posterFallback } from "./poster";

type Notify = (msg: string, kind?: "success" | "error") => void;

const STATUS_BADGE: Record<string, string> = {
  downloaded: "badge badge-success",
  duplicate: "badge badge-info",
  pending: "badge badge-ghost",
  failed: "badge badge-error",
};

function humanBytes(bytes: number): string {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function Images({ notify }: { notify: Notify }) {
  const [stats, setStats] = React.useState<ImageStats | null>(null);
  const [images, setImages] = React.useState<ImageRecord[]>([]);
  const [status, setStatus] = React.useState("downloaded");
  const [host, setHost] = React.useState("");
  const [batch, setBatch] = React.useState("300");
  const [limit, setLimit] = React.useState("60");
  const [offset, setOffset] = React.useState(0);
  const [busy, setBusy] = React.useState(false);
  const [progress, setProgress] = React.useState<DownloadProgress | null>(null);
  const [layout, setLayout] = React.useState<ImageLayout | null>(null);
  const [showErrors, setShowErrors] = React.useState(false);

  const refreshProgress = React.useCallback(() => {
    api
      .downloadProgress()
      .then(setProgress)
      .catch(() => undefined);
  }, []);

  React.useEffect(() => {
    refreshProgress();
    if (!busy) return;
    const timer = setInterval(refreshProgress, 800);
    return () => clearInterval(timer);
  }, [busy, refreshProgress]);

  const load = React.useCallback(async () => {
    const [s, list, buckets] = await Promise.all([
      api.imageStats(),
      api.images({
        ...(status ? { status } : {}),
        ...(host ? { q: host } : {}),
        limit: Number(limit),
        offset,
      }),
      api.imageLayout(12),
    ]);
    setStats(s);
    setImages(list);
    setLayout(buckets);
  }, [status, host, limit, offset]);

  React.useEffect(() => {
    load().catch((e) => notify((e as Error).message, "error"));
  }, [load, notify]);

  const scan = async () => {
    setBusy(true);
    try {
      const r = await api.imageScan();
      notify(`扫描完成：登记 ${r.queued} 张新图片（共扫描 ${r.seen} 个 pic 字段）`);
      load();
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const download = async (retryFailed: boolean) => {
    setBusy(true);
    try {
      const r = await api.imageDownload({ limit: Number(batch), retry_failed: retryFailed });
      notify(
        `下载 ${r.downloaded} 张 · 重复 ${r.duplicates} · 失败 ${r.failed} · 跳过 ${r.skipped} · ${humanBytes(r.bytes)} (${(r.elapsed_ms / 1000).toFixed(1)}s)`,
        r.failed ? "error" : "success"
      );
      load();
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const prune = async () => {
    setBusy(true);
    try {
      const r = await api.imagePrune(true);
      notify(`清理 ${r.removed} 条图片记录，删除 ${r.files_deleted} 个文件`);
      load();
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const repair = async () => {
    setBusy(true);
    try {
      const r = await api.imageRepair();
      notify(`校对索引：修复 ${r.restored} 条状态，重新排队 ${r.requeued} 条，删除 ${r.files_deleted} 个临时文件`);
      load();
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const chips: Array<[string, string, number]> = [
    ["downloaded", "已下载", stats?.by_status?.downloaded ?? 0],
    ["pending", "待下载", stats?.by_status?.pending ?? 0],
    ["duplicate", "重复", stats?.by_status?.duplicate ?? 0],
    ["failed", "失败", stats?.by_status?.failed ?? 0],
  ];

  return (
    <section>
      <div className="stat-grid">
        <div className="stat-card"><b>{stats?.total ?? 0}</b><span>图片记录</span></div>
        <div className="stat-card"><b>{stats?.by_status?.downloaded ?? 0}</b><span>已下载</span></div>
        <div className="stat-card"><b>{stats?.by_status?.pending ?? 0}</b><span>待下载</span></div>
        <div className="stat-card"><b>{stats?.by_status?.failed ?? 0}</b><span>失败</span></div>
        <div className="stat-card"><b>{humanBytes(stats?.bytes ?? 0)}</b><span>磁盘占用</span></div>
        <div className="stat-card"><b>{stats?.shards_used ?? 0}</b><span>已使用分桶</span></div>
      </div>

      <div className="card bg-base-100 border border-base-300 mt-3">
        <div className="card-body">
          <h2 className="card-title">目录分桶策略</h2>
          <p className="muted">
            文件名是图片**内容字节**的 <code>sha256</code>，按前 4 个字符分成两级目录
            （256 × 256 = 65536 个桶），所以即使几十万张海报，每个目录也只有几个文件；
            URL 归一化保证同一链接只登记一次，同一内容不同 URL 只存一份（其余记录标记为 <code>duplicate</code>）。
          </p>
          <div className="code-block">
            {stats?.image_dir ?? "data/images"}/
            {"\n"}  3f/2a/3f2a9c…c1.jpg
            {"\n"}  c4/11/c411bd…e2.webp
            {"\n"}  07/f0/07f0…9d.png
          </div>
          <p className="muted">
            磁盘上实际文件 {stats?.files_on_disk ?? 0} 个 · 数据库是索引，服务通过 <code>/images/&lt;id&gt;</code> 访问本地文件，
            未下载时自动跳转到原外链。
          </p>
          {layout && layout.exists && (
            <div className="mt-2">
              <p className="muted">
                实际分桶：{layout.buckets} 个目录 · 最忙分桶 {layout.busiest} 个文件
              </p>
              <div className="flex flex-wrap gap-1 mt-1">
                {layout.top.map((bucket) => (
                  <span className="badge badge-ghost mono" key={bucket.path}>
                    {bucket.path} · {bucket.files}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mt-3">
        <button className="btn btn-sm btn-outline" onClick={scan} disabled={busy}>扫描图片 URL</button>
        <select className="select input-bordered" value={batch} onChange={(e) => setBatch(e.target.value)} title="每批下载数量">
          <option value="100">100 张</option>
          <option value="300">300 张</option>
          <option value="1000">1000 张</option>
          <option value="5000">5000 张</option>
        </select>
        <button className="btn btn-sm btn-primary" onClick={() => download(false)} disabled={busy}>
          {busy ? "下载中…" : "下载一批"}
        </button>
        <button className="btn btn-sm btn-outline" onClick={() => download(true)} disabled={busy}>
          重试失败的
        </button>
        <button className="btn btn-sm btn-outline" onClick={prune} disabled={busy}>
          清理无引用图片
        </button>
        <button className="btn btn-sm btn-outline" onClick={repair} disabled={busy} title="校对数据库状态与磁盘文件">
          校对索引
        </button>
      </div>
      <p className="muted mt-1">
        「下载一批」只排队 <span className="mono">待下载</span> 的图片：已下载、重复、失败的都不会再请求；
        磁盘上已有文件但索引状态不对的行会被自动修正，不会重新下载。失败项不会自动重试，要重试点「重试失败的」。
      </p>

      <div className="card bg-base-100 border border-base-300 mt-3">
        <div className="card-body">
          <h2 className="card-title">
            下载进度
            {progress?.running ? <span className="badge badge-primary">进行中</span> : null}
          </h2>
          {progress ? (
            <>
              <progress
                className="progress"
                value={progress.downloaded + progress.duplicates + progress.failed + progress.skipped}
                max={progress.queued || 0}
              ></progress>
              <div className="flex flex-wrap gap-2 mt-2">
                <span className="badge">队列 {progress.queued}</span>
                <span className="badge badge-success">成功 {progress.downloaded}</span>
                <span className="badge badge-info">重复 {progress.duplicates}</span>
                <span className="badge badge-error">失败 {progress.failed}</span>
                <span className="badge badge-ghost">跳过 {progress.skipped}</span>
                <span className="muted">{humanBytes(progress.bytes)}</span>
                <span className="muted">{(progress.elapsed_ms / 1000).toFixed(1)}s</span>
                {progress.finished_at && (
                  <span className="muted">结束于 {new Date(progress.finished_at).toLocaleString()}</span>
                )}
              </div>
              {progress.errors.length ? (
                <div className="mt-2">
                  <button className="btn btn-sm btn-ghost" onClick={() => setShowErrors((v) => !v)}>
                    {showErrors ? "隐藏失败原因" : `失败原因 (${progress.errors.length})`}
                  </button>
                  {showErrors && (
                    <ul className="muted mt-1">
                      {progress.errors.slice(0, 10).map((err, i) => (
                        <li key={i}>
                          <span className="mono">{err.host}</span> — {err.error}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
            </>
          ) : (
            <p className="muted">还没有下载记录，点「下载一批」开始。</p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mt-3">
        {chips.map(([key, label, count]) => (
          <button
            key={key}
            className={`btn btn-sm ${status === key ? "btn-primary" : "btn-ghost"}`}
            onClick={() => {
              setStatus(key);
              setOffset(0);
            }}
          >
            {label} {count}
          </button>
        ))}
        <button
          className={`btn btn-sm ${status === "" ? "btn-primary" : "btn-ghost"}`}
          onClick={() => {
            setStatus("");
            setOffset(0);
          }}
        >
          全部
        </button>
        <input
          className="input input-bordered"
          placeholder="按图片域名过滤"
          value={host}
          onChange={(e) => setHost(e.target.value)}
        />
        <select className="select input-bordered" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          <option value="downloaded">已下载</option>
          <option value="pending">待下载</option>
          <option value="duplicate">重复</option>
          <option value="failed">失败</option>
        </select>
      </div>

      <div className="image-list mt-3">
        <table className="table">
          <thead>
            <tr>
              <th>海报</th>
              <th>状态</th>
              <th>域名</th>
              <th>大小</th>
              <th>尝试</th>
              <th>本地路径 / 原始 URL</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {images.map((img) => (
              <tr key={img.id}>
                <td>
                  {img.status === "downloaded" || img.status === "duplicate" ? (
                    <img className="image-thumb" src={`/images/${img.id}`} alt={img.host} loading="lazy" onError={posterFallback} />
                  ) : (
                    <div className="image-thumb image-thumb-empty">{img.status}</div>
                  )}
                </td>
                <td>
                  <span className={STATUS_BADGE[img.status] ?? "badge"}>{img.status}</span>
                </td>
                <td>{img.host}</td>
                <td>{humanBytes(img.size_bytes)}</td>
                <td>{img.attempts}</td>
                <td className="image-path mono">
                  {img.path || img.sha256.slice(0, 16) || "—"}
                  {img.last_error && <span className="muted"> · {img.last_error}</span>}
                </td>
                <td>
                  <a
                    className="btn btn-sm btn-outline"
                    href={img.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    原图
                  </a>
                </td>
              </tr>
            ))}
            {!images.length && (
              <tr>
                <td colSpan={7} className="muted">
                  没有匹配的图片（当前筛选：{status || "全部状态"}）。先点「扫描图片 URL」登记海报链接。
                </td>
              </tr>
            )}
          </tbody>
        </table>

        <div className="flex flex-wrap gap-2 mt-2">
          <select
            className="select input-bordered input-sm"
            value={limit}
            onChange={(e) => {
              setLimit(e.target.value);
              setOffset(0);
            }}
            title="每页显示条数"
          >
            <option value="20">20 条</option>
            <option value="60">60 条</option>
            <option value="100">100 条</option>
            <option value="200">200 条</option>
          </select>
          <button className="btn btn-sm btn-outline" onClick={() => setOffset(Math.max(0, offset - Number(limit)))} disabled={offset === 0}>
            上一页
          </button>
          <span className="muted">
            第 {offset + 1}–{offset + images.length} 条（共 {stats?.total ?? 0} 条记录）
          </span>
          <button
            className="btn btn-sm btn-outline"
            onClick={() => setOffset(offset + Number(limit))}
            disabled={offset + images.length >= (stats?.total ?? 0)}
          >
            下一页
          </button>
        </div>
      </div>

      {stats?.top_hosts?.length ? (
        <div className="card bg-base-100 border border-base-300 mt-3">
          <div className="card-body">
            <h2 className="card-title">图片域名 Top 10</h2>
            <table className="table">
              <thead>
                <tr><th>域名</th><th>图片数</th></tr>
              </thead>
              <tbody>
                {stats.top_hosts.map((h) => (
                  <tr key={h.host}>
                    <td>{h.host}</td>
                    <td>{h.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}
