import React from "react";

/**
 * Inline player. Most resource-site lines are HLS (.m3u8), which Chrome/Firefox
 * cannot play natively, so hls.js is loaded on demand and the browser's own HLS
 * support is used on Safari. Anything else is handed straight to <video>.
 *
 * Playback starts on its own, and again whenever the line or episode changes.
 * Browsers refuse autoplay with sound without a user gesture, so the player
 * falls back to muted. Only if that fails too does the "click to start" hint
 * appear — and it disappears as soon as frames actually start flowing.
 */
export default function Player({ url, poster }: { url: string; poster?: string | null }) {
  const ref = React.useRef<HTMLVideoElement>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [needsGesture, setNeedsGesture] = React.useState(false);
  const [mutedStart, setMutedStart] = React.useState(false);

  React.useEffect(() => {
    const video = ref.current;
    if (!video) return;
    setError(null);
    setNeedsGesture(false);

    let hls: { destroy: () => void } | null = null;
    const isHls = /\.m3u8(\?.*)?$/i.test(url);

    const onError = () => setError("这条线路无法直接播放，换一条线路试试");
    // Any real playback clears the autoplay hint, even if play() reported a
    // failure earlier (some browsers start the stream anyway).
    const onPlaying = () => {
      setNeedsGesture(false);
      setError(null);
      // Autoplay is only allowed muted, so flag that the sound is off.
      setMutedStart(video.muted);
    };
    // Keep the "sound is off" pill in sync with the native mute button.
    const onMuteChange = () => setMutedStart(video.muted && !video.paused);

    video.addEventListener("error", onError);
    video.addEventListener("playing", onPlaying);
    video.addEventListener("mute", onMuteChange);
    video.addEventListener("unmute", onMuteChange);

    const start = () => {
      video.play().catch(() => {
        video.muted = true;
        video.play().catch(() => {
          if (video.paused) setNeedsGesture(true);
        });
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
      video.removeEventListener("playing", onPlaying);
      video.removeEventListener("mute", onMuteChange);
      video.removeEventListener("unmute", onMuteChange);
      if (hls) hls.destroy();
    };
  }, [url]);

  // Clicking the hint is a user gesture, so try with sound first.
  const resume = () => {
    const video = ref.current;
    if (!video) return;
    video.muted = false;
    video.play().catch(() => {
      video.muted = true;
      video.play().catch(() => setError("这条线路无法直接播放，换一条线路试试"));
    });
  };

  const unmute = () => {
    const video = ref.current;
    if (!video) return;
    video.muted = false;
    setMutedStart(false);
    video.play().catch(() => undefined);
  };

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
      {needsGesture && (
        <button type="button" className="player-hint" onClick={resume}>
          <span className="player-hint-mark">▶</span>
          浏览器拦截了自动播放，点击这里开始播放
        </button>
      )}
      {mutedStart && !needsGesture && (
        <button type="button" className="player-unmute" onClick={unmute}>
          静音播放中，点击开启声音
        </button>
      )}
      {error && !needsGesture && (
        <div className="player-error">
          <span className="muted">{error}</span>
        </div>
      )}
    </div>
  );
}
