import React, {useEffect, useRef, useState} from "react";
import {AbsoluteFill, cancelRender, continueRender, delayRender, interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";
import {CaptionOverlay} from "./CaptionOverlay";

const NOTICE_TEXT = {
  unsafe_unverified_not_advice: "未核验高风险观点，不是操作建议",
  synthetic_demo: "合成演示",
  truncated_sample: "评论未完整采集",
  high_risk_needs_review: "高风险 · 发布前人工复核",
  experience_not_fact: "评论个案 ≠ 普遍事实",
  ai_assisted: "AI 辅助整理",
};

export const SceneShell = ({scene, video, eyebrow, children}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const contentRef = useRef(null);
  const [layoutHandle] = useState(() => delayRender(`measure ${scene.scene_id}`));
  const enter = spring({frame, fps, config: {damping: 18, mass: 0.7, stiffness: 95}});
  const sceneFrames = Math.round((scene.end_ms - scene.start_ms) * fps / 1000);
  const exit = interpolate(frame, [Math.max(0, sceneFrames - 10), sceneFrames], [1, 0], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  const nowMs = scene.start_ms + frame * 1000 / fps;
  const caption = scene.captions.find((item) => nowMs >= item.startMs && nowMs < item.endMs);

  useEffect(() => {
    const frameId = requestAnimationFrame(() => {
      const node = contentRef.current;
      if (!node || node.clientWidth === 0 || node.clientHeight === 0) {
        cancelRender(new Error(`VIDEO_LAYOUT_UNAVAILABLE(${scene.scene_id})`));
        return;
      }
      if (node.scrollHeight > node.clientHeight + 2 || node.scrollWidth > node.clientWidth + 2) {
        cancelRender(new Error(`VIDEO_LAYOUT_OVERFLOW(${scene.scene_id}): content ${node.scrollWidth}x${node.scrollHeight} exceeds ${node.clientWidth}x${node.clientHeight}`));
        return;
      }
      const root = node.parentElement;
      for (const probe of root?.querySelectorAll(".dynamic-field-probe") || []) {
        if (probe.scrollHeight > probe.clientHeight + 2 || probe.scrollWidth > probe.clientWidth + 2) {
          cancelRender(new Error(`VIDEO_LAYOUT_OVERFLOW(${scene.scene_id}): dynamic content ${probe.scrollWidth}x${probe.scrollHeight} exceeds ${probe.clientWidth}x${probe.clientHeight}`));
          return;
        }
      }
      for (const captionNode of root?.querySelectorAll(".caption-probe") || []) {
        if (captionNode.scrollHeight > captionNode.clientHeight + 2 || captionNode.scrollWidth > captionNode.clientWidth + 2) {
          cancelRender(new Error(`CAPTION_OVERFLOW(${scene.scene_id}): content ${captionNode.scrollWidth}x${captionNode.scrollHeight} exceeds ${captionNode.clientWidth}x${captionNode.clientHeight}`));
          return;
        }
      }
      continueRender(layoutHandle);
    });
    return () => cancelAnimationFrame(frameId);
  }, [layoutHandle, scene.scene_id]);

  const warning = scene.persistent_notices.includes("unsafe_unverified_not_advice");
  const minorNotices = scene.persistent_notices.filter((code) => code !== "unsafe_unverified_not_advice");
  const progress = Math.min(100, Math.max(0, scene.end_ms / video.duration_ms * 100));
  return (
    <AbsoluteFill className={`video-canvas role-${scene.role}`} style={{opacity: exit}} data-scene-id={scene.scene_id}>
      <div className="ambient ambient-a" style={{transform: `translate3d(${(1 - enter) * -90}px, ${(1 - enter) * 40}px, 0)`}} />
      <div className="ambient ambient-b" style={{transform: `translate3d(${(1 - enter) * 100}px, ${(1 - enter) * -50}px, 0)`}} />
      <div className="top-progress"><span style={{width: `${progress}%`}} /></div>
      <header className="scene-header">
        <div className="eyebrow">{eyebrow}</div>
        <div className="scene-count">{String(scene.index).padStart(2, "0")} / {String(video.scenes.length).padStart(2, "0")}</div>
      </header>
      {warning ? <div className="persistent-warning">{NOTICE_TEXT.unsafe_unverified_not_advice}</div> : null}
      <main className="scene-content" ref={contentRef} style={{transform: `translateY(${(1 - enter) * 36}px)`, opacity: enter}}>
        {children}
      </main>
      <div className="scene-notices">
        {minorNotices.map((code) => <span className={`notice-chip notice-${code}`} key={code}>{NOTICE_TEXT[code]}</span>)}
      </div>
      {scene.evidence_comment_ids.length ? (
        <div className="evidence-strip"><span>证据定位</span>{scene.evidence_comment_ids.map((id) => <b key={id}>#{id}</b>)}</div>
      ) : null}
      <CaptionOverlay caption={caption} sceneId={scene.scene_id} />
      <div aria-hidden="true">{scene.captions.map((item, index) => <div className="caption-card caption-probe" key={index}>{item.text}</div>)}</div>
      <div className="audio-disclosure">无配音版 · 静音也能看懂</div>
    </AbsoluteFill>
  );
};
