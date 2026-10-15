import React from "react";
import { api, type ImageRecord, type ImageStats } from "./api";

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
  const [status, setStatus] = React.useState("");
  const [host, setHost] = React.useState("");
  const [batch, setBatch] = React.useState("300");
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    const [s, list] = await Promise.all([
      api.imageStats(),
      api.images({
        ...(status ? { status } : {}),
        ...(host ? { q: host } : {}),
        limit: 60,
      }),
    ]);
    setStats(s);
    setImages(list);
  }, [status, host]);

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

      <div className="flex flex-wrap gap-2 mt-3">
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

      <div className="image-grid mt-3">
        {images.map((img) => (
          <div key={img.id} className="image-tile">
            {img.status === "downloaded" || img.status === "duplicate" ? (
              <img src={`/images/${img.id}`} alt={img.host} loading="lazy" />
            ) : (
              <div className="image-placeholder">{img.status}</div>
            )}
            <div className="image-meta">
              <span className={STATUS_BADGE[img.status] ?? "badge"}>{img.status}</span>
              <span className="muted">{img.host}</span>
              <span className="muted">{humanBytes(img.size_bytes)}</span>
            </div>
            <div className="image-path mono">{img.path || img.sha256.slice(0, 16) || "—"}</div>
          </div>
        ))}
        {!images.length && <div className="muted">没有图片记录，先运行「扫描图片 URL」。</div>}
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
