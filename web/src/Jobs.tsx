import React from "react";
import { api, type Job, type Source } from "./api";

type Notify = (msg: string, kind?: "success" | "error") => void;

export default function Jobs({ notify }: { notify: Notify }) {
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [sources, setSources] = React.useState<Source[]>([]);

  const load = () =>
    Promise.all([api.jobs(100), api.sources()])
      .then(([j, s]) => {
        setJobs(j);
        setSources(s);
      })
      .catch((e) => notify((e as Error).message, "error"));

  React.useEffect(() => {
    load();
  }, []);

  const nameOf = (id: number) => sources.find((s) => s.id === id)?.name ?? `#${id}`;

  return (
    <section>
      <div className="toolbar">
        <button className="btn btn-sm btn-outline" onClick={load}>刷新</button>
        <span className="muted">{jobs.length} 条采集记录</span>
      </div>

      <div className="card bg-base-100 border border-base-300">
        <div className="card-body">
          <table className="table">
            <thead>
              <tr>
                <th>站点</th>
                <th>开始</th>
                <th>结束</th>
                <th>页数</th>
                <th>新增</th>
                <th>更新</th>
                <th>跳过</th>
                <th>错误</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>{nameOf(j.source_id)}</td>
                  <td className="muted">{new Date(j.started_at).toLocaleString()}</td>
                  <td className="muted">{j.finished_at ? new Date(j.finished_at).toLocaleString() : "进行中"}</td>
                  <td>{j.pages}</td>
                  <td>{j.inserted}</td>
                  <td>{j.updated}</td>
                  <td>{j.skipped}</td>
                  <td className="text-error">{j.error ?? ""}</td>
                </tr>
              ))}
              {jobs.length === 0 && <tr><td className="muted">暂无记录</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
