import React from "react";
import {interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";

const SAFETY_WARNING = "未核验高风险观点，不是操作建议";

export const CaptionOverlay = ({caption, sceneId, sceneStartMs}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const startFrame = caption ? Math.round((caption.startMs - sceneStartMs) * fps / 1000) : 0;
  const enter = caption ? spring({frame: frame - startFrame, fps, config: {damping: 24, stiffness: 155, mass: 0.55}}) : 0;
  const safety = Boolean(caption?.text.startsWith(SAFETY_WARNING));
  const opacity = caption ? interpolate(enter, [0, 1], [0, 1]) : 0;
  return <div
    className={`caption-card ${caption ? "caption-visible" : ""} ${safety ? "caption-safety" : ""}`}
    data-caption-scene={sceneId}
    style={{
      opacity: safety ? 1 : opacity,
      translate: `0px ${interpolate(enter, [0, 1], [18, 0])}px`,
      scale: interpolate(enter, [0, 1], [0.975, 1]),
    }}
  >{caption?.text || ""}</div>;
};
