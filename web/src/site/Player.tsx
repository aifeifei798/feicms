import React from "react";

/**
 * Inline player. Most resource-site lines are HLS (.m3u8), which Chrome/Firefox
 * cannot play natively, so hls.js is loaded on demand and the browser's own HLS
 * support is used on Safari. Anything else is handed straight to <video>.
 *
 * Playback starts on its own, and again whenever the line or episode changes.
 * Browsers refuse autoplay with sound without a user gesture, so the player
 * falls back to muted instead of staying still.
 */
export default function Player({ url, poster }: { url: string; poster?: string | null }) {
  const ref = React.useRef<HTMLVideoElement>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const video = ref.current;
    if (!video) return;
    setError(null);

    let hls: { destroy: () => void } | null = null;
    const isHls = /\.m3u8(\?.*)?$/i.test(url);

    const onError = () => setError("这条线路无法直接播放，换一条线路试试");
    video.addEventListener("error", onError);

    const start = () => {
      video.play().catch(() => {
        video.muted = true;
        video.play().catch(() => setError("浏览器拦截了自动播放，点击播放器即可开始"));
      });
    };

    if (isHls) {
      // hls.js is only pulled in when a line actually needs it.
      import("hls.js").then(({ default: Hls }) => {
        if (video.canPlayType("application/vnd.apple.mpegurl")) {
          video.src = url;
          start();
          return;
        }
        if (!Hls.isSupported()) {
          setError("浏览器不支持 HLS 播放，换一条线路试试");
          return;
        }
        const player = new Hls({ lowLatencyMode: true });
        hls = player;
        player.loadSource(url);
        player.attachMedia(video);
        player.on(Hls.Events.MANIFEST_PARSED, () => start());
        player.on(Hls.Events.ERROR, (_event, data) => {
          if (data.fatal) setError(`播放失败（${data.type}），换一条线路试试`);
        });
      });
    } else {
      video.src = url;
      start();
    }

    return () => {
      video.removeEventListener("error", onError);
      if (hls) hls.destroy();
    };
  }, [url]);

  return (
    <div className="player-wrap">
      <video
        ref={ref}
        controls
        playsInline
        autoPlay
        preload="metadata"
        poster={poster || undefined}
      >
        <p className="muted">浏览器不支持内嵌播放。</p>
      </video>
      {error && (
        <div className="player-error">
          <span className="muted">{error}</span>
        </div>
      )}
    </div>
  );
}
