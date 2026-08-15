import {interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";

export const FOCUS_INTERVAL_SECONDS = 2.4;

export const sceneFocusInterval = (scene, itemCount) => Math.min(
  FOCUS_INTERVAL_SECONDS,
  (scene.end_ms - scene.start_ms) / 1000 / Math.max(1, itemCount),
);

export const useFocusMotion = (itemCount, intervalSeconds = FOCUS_INTERVAL_SECONDS) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const count = Math.max(1, itemCount);
  const intervalFrames = Math.max(1, Math.round(fps * Math.min(FOCUS_INTERVAL_SECONDS, intervalSeconds)));
  const step = Math.floor(frame / intervalFrames);
  const active = step % count;
  const previous = (active - 1 + count) % count;
  const localFrame = frame - step * intervalFrames;
  const entering = step === 0 ? 1 : spring({
    frame: localFrame,
    fps,
    config: {damping: 22, stiffness: 125, mass: 0.62},
  });

  const styleFor = (index) => {
    const isIncoming = index === active;
    const motion = isIncoming ? entering : 0;
    return {
      opacity: isIncoming ? 1 : 0,
      translate: `0px ${interpolate(motion, [0, 1], [24, 0])}px`,
      scale: interpolate(motion, [0, 1], [0.985, 1]),
    };
  };

  const emphasisStyleFor = (index) => {
    const isIncoming = index === active;
    const isOutgoing = step > 0 && index === previous;
    const emphasis = isIncoming ? entering : isOutgoing ? 1 - entering : 0;
    return {
      opacity: interpolate(emphasis, [0, 1], [0.7, 1]),
      translate: `0px ${interpolate(emphasis, [0, 1], [8, 0])}px`,
      scale: interpolate(emphasis, [0, 1], [0.98, 1]),
    };
  };

  return {active, styleFor, emphasisStyleFor};
};
